# -*- coding: utf-8 -*-
"""github.com:443 不通时，用 GitHub 的 git 数据接口把本地 HEAD 提交推上去。

本项目所在网络经常连不上 github.com:443（连接被重置），但 api.github.com 正常，
因此常规的 git push 会失败，用这个脚本代替：

    py -3 tools/push_via_api.py

做法与保证：
1. 把本地 HEAD 的整棵树按原样重建到远端，远端已有的 blob 直接复用，只上传新文件；
2. 提交的作者、时间、信息都取自本地提交对象，远端会得到与本地完全相同的 commit SHA；
3. 推送前会校验“远端分支是本地 HEAD 的祖先”，避免覆盖别人的提交；
4. 令牌从 git 凭据管理器读取（`git credential fill`），不落盘、不写进仓库。

覆盖语义：脚本按本地 HEAD 重建远端分支，因此本地没有的文件在远端也会消失，
即“上传新的会覆盖之前的”。这也是本项目约定的更新方式：

    git add -A
    git commit -m "说明这次改了什么"
    py -3 tools/push_via_api.py

注意：如果队友已经推了新提交，先 `git fetch`/让队友同步，不要在未合并的情况下强推。

实现细节：读取文件列表必须用 `git ls-tree -r -z`。默认输出会把含非 ASCII 的路径
做成 C 风格引号转义（如 `"docs/PDF\\350...docx"`），直接解析会在远端写出错误目录名。
"""

import base64
import io
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

REPO_DIR = sys.argv[1] if len(sys.argv) > 1 else r'D:\python\研报纠错助手：基于大模型与结构化比对的上市公司研究报告自动核查工具'
OWNER = 'lomooc-kk'
REPO = 'Large-Model-Automatic-Verification-Tool-for-Listed-Firms-Research-Reports-via-Structural-Opt.'
BRANCH = 'main'
API = f'https://api.github.com/repos/{OWNER}/{REPO}'


def git(*args, binary=False):
    result = subprocess.run(['git', '-C', REPO_DIR, *args], capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{result.stderr.decode('utf-8', 'replace')}")
    return result.stdout if binary else result.stdout.decode('utf-8', 'replace').strip()


def token():
    result = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    for line in result.stdout.splitlines():
        if line.startswith('password='):
            return line[len('password='):].strip()
    raise SystemExit('取不到 GitHub 令牌')


HEADERS = {
    'Authorization': 'Bearer ' + token(),
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'codex',
    'X-GitHub-Api-Version': '2022-11-28',
}


def call(method, url, payload=None, allow_404=False):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read().decode('utf-8')
    except urllib.error.HTTPError as error:
        if allow_404 and error.code == 404:
            return None
        raise SystemExit(f'{method} {url} -> {error.code} {error.read().decode("utf-8", "replace")[:300]}')
    return json.loads(body) if body else {}


head = git('rev-parse', 'HEAD')
raw_commit = git('cat-file', '-p', head)
parent = re.search(r'^parent ([0-9a-f]{40})$', raw_commit, re.M).group(1)
author = re.search(r'^author (.+)$', raw_commit, re.M).group(1)
committer = re.search(r'^committer (.+)$', raw_commit, re.M).group(1)
# 提交信息结尾的换行要原样保留：少了它算出来的 SHA 会和本地不一致
message = raw_commit.split('\n\n', 1)[1]


def split_ident(line):
    match = re.match(r'^(.*) <(.*)> (\d+) ([+-]\d{4})$', line)
    name, email, epoch, tz = match.groups()
    # 用 log 而不是 show：show 会去读父提交的 tree，父提交对象缺失时会整条失败
    date = subprocess.run(['git', '-C', REPO_DIR, 'log', '-1', '--date=iso-strict-local',
                           '--format=%ad', head], capture_output=True, text=True,
                          encoding='utf-8', errors='replace').stdout.strip()
    return {'name': name, 'email': email, 'date': date}


remote = call('GET', f'{API}/git/ref/heads/{BRANCH}')
remote_sha = remote['object']['sha']
print('本地 HEAD :', head)
print('远端 main :', remote_sha)
if remote_sha == head:
    raise SystemExit('远端已经是这个提交，无需推送')
ancestor = subprocess.run(['git', '-C', REPO_DIR, 'merge-base', '--is-ancestor', remote_sha, head])
if ancestor.returncode != 0:
    # 常见情形：队友用 GitHub 网页合并产生了 merge 提交，本地因为 github.com:443 不通
    # 没能 fetch 到该对象，导致祖先判断失败。此时比较内容的 tree：
    # 若远端提交的 tree 已经出现在本地历史里，说明内容已包含，可以安全推送。
    remote_tree = call('GET', f'{API}/git/commits/{remote_sha}')['tree']['sha']
    # 注意：不能用 git log ——历史里可能引用了本地缺失的远端对象（见上），
    # 遍历父提交会直接报错。这里逐个尝试最近几级的 tree，取到即用。
    local_trees = set()
    for depth in range(5):
        for expr in (f'HEAD~{depth}^{{tree}}', 'HEAD^{tree}'):
            probe = subprocess.run(['git', '-C', REPO_DIR, 'rev-parse', '--verify', expr],
                                   capture_output=True, text=True, encoding='utf-8',
                                   errors='replace')
            if probe.returncode == 0:
                local_trees.add(probe.stdout.strip())
                break
    if remote_tree not in local_trees:
        raise SystemExit('远端提交不是本地 HEAD 的祖先，先人工确认，避免覆盖别人的提交')
    print(f'提示：远端提交对象本地缺失，但其内容 tree {remote_tree[:8]} 已在本地历史中，'
          f'判定为可安全推送')

# 收集 HEAD 的全部 blob。
# 必须用 -z：默认输出会对含非 ASCII 的路径做 C 风格引号转义（如 "docs/PDF\350...docx"），
# 直接解析会把目录名写成 "\"docs"，文件名写成转义串。
entries = []
for record in git('ls-tree', '-r', '-z', 'HEAD').split('\0'):
    if not record:
        continue
    meta, path = record.split('\t', 1)
    mode, kind, sha = meta.split()
    assert kind == 'blob', record
    entries.append((mode, path, sha))
print('文件数:', len(entries))

# 远端没有的 blob 才上传
uploaded = 0
for mode, path, sha in entries:
    if call('GET', f'{API}/git/blobs/{sha}', allow_404=True) is None:
        content = base64.b64encode(git('cat-file', 'blob', sha, binary=True)).decode('ascii')
        call('POST', f'{API}/git/blobs', {'content': content, 'encoding': 'base64'})
        uploaded += 1
print('新上传 blob:', uploaded)


def build_tree(items, prefix=''):
    """items: (mode, path, blob_sha) -> 远端 tree sha"""
    files, dirs = [], {}
    for mode, path, sha in items:
        rest = path[len(prefix):] if prefix else path
        if '/' in rest:
            head_dir, tail = rest.split('/', 1)
            dirs.setdefault(head_dir, []).append((mode, path, sha))
        else:
            files.append({'path': rest, 'mode': mode, 'type': 'blob', 'sha': sha})
    tree = list(files)
    for name in sorted(dirs):
        sub_sha = build_tree(dirs[name], (prefix + name + '/') if prefix else (name + '/'))
        tree.append({'path': name, 'mode': '040000', 'type': 'tree', 'sha': sub_sha})
    tree.sort(key=lambda item: item['path'] + ('/' if item['type'] == 'tree' else ''))
    return call('POST', f'{API}/git/trees', {'tree': tree})['sha']


tree_sha = build_tree(entries)
print('root tree:', tree_sha)

payload = {'message': message, 'tree': tree_sha, 'parents': [remote_sha],
           'author': split_ident(author), 'committer': split_ident(committer)}
created = call('POST', f'{API}/git/commits', payload)['sha']
print('远端新提交:', created)
print('与本地一致:', created == head)

if created != head:
    # GitHub 会把提交信息结尾的换行去掉（偶尔还会改时区写法），它算出来的 SHA
    # 因此会和本地差一位。这里把等价的对象在本地重建出来，让两边重新对齐，
    # 否则下一次推送会因为找不到父提交而失败。
    def write_object(message_variant):
        body = (f'tree {tree_sha}\n'
                f'parent {remote_sha}\n'
                f'author {author}\n'
                f'committer {committer}\n\n{message_variant}')
        result = subprocess.run(['git', '-C', REPO_DIR, 'hash-object', '-w', '-t', 'commit', '--stdin'],
                                input=body.encode('utf-8'), capture_output=True)
        return result.stdout.decode().strip() if result.returncode == 0 else None

    for variant in (message, message.rstrip('\n'), message.strip('\n')):
        sha = write_object(variant)
        if sha == created:
            git('update-ref', f'refs/heads/{BRANCH}', sha)
            print('提交信息结尾换行被 GitHub 去掉，已把本地分支对齐到远端提交')
            break
    else:
        print('警告：没能重建出一致的本地提交对象，请人工检查后再推送。')

call('PATCH', f'{API}/git/refs/heads/{BRANCH}', {'sha': created, 'force': False})
if git('rev-parse', 'HEAD') == created:
    git('update-ref', f'refs/remotes/origin/{BRANCH}', created)
    print('远端 main 已更新；本地 origin/main 已对齐')
else:
    print('远端 main 已更新，但本地提交号仍不一致（见上面的警告）')
