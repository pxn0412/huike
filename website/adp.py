"""把切好的文本送进腾讯云智能体开发平台（ADP）知识库。

官方文档位置：API 中心 → 腾讯云智能体开发平台 → 知识库相关接口 → 「添加文档 SaveDoc」，
要求三步：
    1. DescribeStorageCredential 取临时密钥与上传路径
    2. 用临时密钥把文件 PUT 到平台 COS，拿到 ETag 与 CRC64
    3. SaveDoc 登记文档，并把切分规则一起下发（按标识符 --- 切、800 字、重叠 80）

用法：
    python adp.py --all                                     # 提交 docs/adp/data 下所有上传稿
    python adp.py --file ../docs/adp/data/xxx.md --dry-run   # 只打印将要发的内容，不联网
    python adp.py --check                                   # 只检查密钥与知识库配置
    python adp.py --list                                    # 列出知识库现有文档
    python adp.py --verify "一个问题"                        # 用 AppKey 验证资料是否已在知识库

配置（写在 website/.env 或环境变量里，不要提交进仓库）：
    TENCENT_SECRET_ID / TENCENT_SECRET_KEY   腾讯云 API 密钥（AKID 开头 36 位 / 32 位）
    ADP_KB_ID                                知识库 ID（新版接口字段名 KbId）
    ADP_REGION                               地域，默认 ap-guangzhou

注意：ADP_APP_KEY 只能问答，不能上传；上传必须用腾讯云 API 密钥。
      DescribeStorageCredential 的返回字段名以平台实际返回为准，脚本会做兼容取值；
      第一次跑建议先 --dry-run，再真跑并把打印出来的密钥字段核对一遍。
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT.parent / 'docs' / 'adp' / 'data'
# 新版、旧版是两套接口。实测（2026-09-19）：文档上传走旧版 lke 这套 —— 官方文档「离线文档上传」
# 就是 DescribeStorageCredential → COS → SaveDoc；新版 adp 那套里没有 SaveDoc（ImportDocList 需要
# 另一个“文件管理服务”给的文件标识，公开 API 里没有），所以默认用 old。
API_MODES = {'new': ('adp.tencentcloudapi.com', 'adp', '2026-05-20'),
             'old': ('lke.tencentcloudapi.com', 'lke', '2023-11-30')}
DEFAULT_API = 'old'
DEFAULT_TAG = '---'
DEFAULT_CHUNK_LENGTH = 800
DEFAULT_OVERLAP = 80
SETTING_NAMES = ('TENCENT_SECRET_ID', 'TENCENT_SECRET_KEY', 'ADP_KB_ID', 'ADP_REGION',
                 'ADP_AUTO_UPLOAD')


def settings():
    """先读环境变量，再读 .env；和 ai.py 的取值方式保持一致。"""
    values = {}
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return {name: os.environ.get(name, values.get(name, '')) for name in SETTING_NAMES}


def missing_settings():
    values = settings()
    return [name for name in ('TENCENT_SECRET_ID', 'TENCENT_SECRET_KEY', 'ADP_KB_ID') if not values[name]]


def split_rule(tag=DEFAULT_TAG, chunk_length=DEFAULT_CHUNK_LENGTH, overlap=DEFAULT_OVERLAP):
    """对应平台的“通用标识符切分”：标识符不出现在切片里。"""
    return json.dumps({'split_config_new': {
        'table_style': 'md', 'rm_spec_symbol': 1,
        'common_splitter': {'splitter': 'tag',
                            'tag_splitter': {'tag': [tag], 'chunk_length': chunk_length,
                                             'chunk_overlap_length': overlap}}}}, ensure_ascii=False)


def sign(key, message):
    return hmac.new(key, message.encode('utf-8'), hashlib.sha256).digest()


def api_call(action, payload, region, secret_id, secret_key, api=DEFAULT_API):
    """腾讯云 API 3.0 签名（TC3-HMAC-SHA256），只用标准库。"""
    host, service, version = API_MODES[api]
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    timestamp = int(time.time())
    date = time.strftime('%Y-%m-%d', time.gmtime(timestamp))
    signed_headers = 'content-type;host;x-tc-action'
    canonical = '\n'.join([
        'POST', '/', '',
        'content-type:application/json; charset=utf-8',
        f'host:{host}', f'x-tc-action:{action.lower()}', '',
        signed_headers, hashlib.sha256(body.encode('utf-8')).hexdigest()])
    scope = f'{date}/{service}/tc3_request'
    to_sign = '\n'.join(['TC3-HMAC-SHA256', str(timestamp), scope,
                         hashlib.sha256(canonical.encode('utf-8')).hexdigest()])
    secret_date = sign(('TC3' + secret_key).encode('utf-8'), date)
    secret_service = sign(secret_date, service)
    secret_signing = sign(secret_service, 'tc3_request')
    signature = hmac.new(secret_signing, to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {'Authorization': f'TC3-HMAC-SHA256 Credential={secret_id}/{scope}, '
                                f'SignedHeaders={signed_headers}, Signature={signature}',
               'Content-Type': 'application/json; charset=utf-8',
               'Host': host, 'X-TC-Action': action, 'X-TC-Timestamp': str(timestamp),
               'X-TC-Version': version}
    if region:
        headers['X-TC-Region'] = region
    request = urllib.request.Request(f'https://{host}', data=body.encode('utf-8'), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response).get('Response', {})
    except urllib.error.HTTPError as exc:
        raise ValueError(f'{action} 调用失败（HTTP {exc.code}）：'
                         f'{exc.read().decode("utf-8", "replace")[:400]}') from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f'{action} 网络不可达：{exc}') from exc
    if result.get('Error'):
        error = result['Error']
        raise ValueError(f"{action} 返回错误：{error.get('Code')} {error.get('Message')}")
    return result


def cos_authorization(secret_id, secret_key, method, path, host, seconds=600):
    """COS 临时密钥签名（q-sign-algorithm=sha1）。

    两个必须照官方 SDK 做的细节（2026-09-19 实测，错一个就报 SignatureDoesNotMatch）：
    1. sign_key 取 hexdigest（不是 digest）——见 qcloud_cos/cos_auth.py；
    2. path 用未转义的原文，COS 侧同样按原文校验。
    """
    key = secret_key if isinstance(secret_key, bytes) else secret_key.encode('utf-8')
    start = int(time.time()) - 60
    key_time = f'{start};{start + seconds}'
    sign_key = hmac.new(key, key_time.encode('utf-8'), hashlib.sha1).hexdigest()
    http_string = f'{method.lower()}\n{path}\n\nhost={urllib.parse.quote(host)}\n'
    to_sign = f'sha1\n{key_time}\n{hashlib.sha1(http_string.encode("utf-8")).hexdigest()}\n'
    signature = hmac.new(sign_key.encode('utf-8'), to_sign.encode('utf-8'), hashlib.sha1).hexdigest()
    return (f'q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}'
            f'&q-key-time={key_time}&q-header-list=host&q-url-param-list='
            f'&q-signature={signature}')


def upload_to_cos(credential, content, upload_path):
    """把文件 PUT 到平台分配的上传路径（UploadPath 就是 COS 的 key）。"""
    inner = credential.get('Credentials') or credential
    host = f"{credential['Bucket']}.cos.{credential['Region']}.myqcloud.com"
    headers = {'Authorization': cos_authorization(inner['TmpSecretId'], inner['TmpSecretKey'],
                                                  'put', upload_path, host),
               'Host': host, 'Content-Type': 'application/octet-stream',
               'x-cos-security-token': inner['Token'],
               'Content-Length': str(len(content))}
    request = urllib.request.Request(f'https://{host}{urllib.parse.quote(upload_path)}', data=content,
                                     headers=headers, method='PUT')
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            etag = response.headers.get('ETag', '')
            crc64 = response.headers.get('x-cos-hash-crc64ecma', '')
    except urllib.error.HTTPError as exc:
        raise ValueError(f'上传平台 COS 失败（HTTP {exc.code}）：'
                         f'{exc.read().decode("utf-8", "replace")[:400]}') from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f'上传平台 COS 网络不可达：{exc}') from exc
    return etag, crc64, len(content)


def credential_parts(credential):
    """取临时密钥与平台分配的 UploadPath（新版放在 StoragePath 里，旧版在顶层）。

    官方文档「离线文档上传」：请求要带 BotBizId、FileType、TypeKey=offline、IsPublic=false，
    平台才会返回可写的 UploadPath（形如 /corp/<uin>/<知识库 ID>/doc/xxx.md）。
    """
    inner = credential.get('Credentials') or credential
    storage = credential.get('StoragePath') or {}
    upload_path = (credential.get('UploadPath') or storage.get('UploadPath') or '').strip()
    return inner, upload_path


def save_payload(filename, cos_url, etag, crc64, size, kb_id, enable_scope, rule, kb_field='BotBizId'):
    """SaveDoc 的请求体。

    DuplicateFileHandles 交给平台按**内容哈希**判重（CheckType=1 按 cos_hash，
    HandleType=2 跳过并返回重复文档的业务 ID）：同一份内容在知识库里只该有一份。
    本地那份“按原始 PDF 哈希判重”仍然是主力，这里是远端兜底。
    """
    return {kb_field: kb_id, 'FileName': filename,
            'FileType': Path(filename).suffix.lstrip('.').lower(),
            'CosUrl': cos_url, 'ETag': etag, 'CosHash': crc64, 'Size': str(size),
            'AttrRange': 1, 'Source': 0, 'Opt': 2, 'EnableScope': enable_scope,
            'IsRefer': True, 'SplitRule': rule,
            'DuplicateFileHandles': [{'CheckType': 1, 'HandleType': 2}]}


def submit(source, filename, args, config, kb_id, region, kb_field, rule):
    """一个文件的完整提交（官方「离线文档上传」三步，2026-09-19 实测跑通）。

    1. DescribeStorageCredential：带 BotBizId + FileType + TypeKey=offline + IsPublic=false，
       平台才返回只有上传权限的临时密钥和本次专属的 UploadPath；
    2. 用临时密钥把文件 PUT 到 UploadPath，取回 ETag 与 CRC64；
    3. SaveDoc 登记元数据（CosUrl 就是第 1 步的 UploadPath）。
    """
    content = source.read_bytes()
    file_type = Path(filename).suffix.lstrip('.').lower()
    secret_id, secret_key = config['TENCENT_SECRET_ID'], config['TENCENT_SECRET_KEY']
    credential = api_call('DescribeStorageCredential',
                          {kb_field: kb_id, 'FileType': file_type,
                           'TypeKey': args.type_key, 'IsPublic': args.public},
                          region, secret_id, secret_key, args.api)
    _, upload_path = credential_parts(credential)
    if not upload_path:
        raise ValueError('平台没有返回 UploadPath：请确认 BotBizId、FileType、TypeKey 都传了。')
    print(f'  平台分配上传路径：{upload_path}')
    etag, crc64, size = upload_to_cos(credential, content, upload_path)
    print(f'  已上传 COS：ETag {etag}｜CRC64 {crc64}')
    payload = save_payload(filename, upload_path, etag, crc64, size, kb_id, args.enable_scope,
                           rule, kb_field)
    result = api_call('SaveDoc', payload, region, secret_id, secret_key, args.api)
    print(f"  已登记：DocBizId {result.get('DocBizId')}｜重复检查 {result.get('DuplicateFileCheckType')}")
    if result.get('ErrorMsg'):
        print(f"  平台提示：{result['ErrorMsg']}")
    return result


def describe_documents(kb_id, region):
    """知识库里现有的文档：[{'doc_id', 'name', 'status'}]（新版 DescribeDocSummaryList）。

    平台在回答的引用里给的是 DocBizId/DocId，本地要能反查，就必须先把
    “知识库文档 ID ↔ 本地资料”记下来（见 store.link_knowledge_documents）。
    """
    values = settings()
    result = api_call('DescribeDocSummaryList', {'KbId': kb_id}, region,
                      values['TENCENT_SECRET_ID'], values['TENCENT_SECRET_KEY'], 'new')
    documents = []
    for item in result.get('DocList', []):
        name = (item.get('Metadata') or {}).get('FileName') or ''
        doc_id = str(item.get('DocId') or '')
        if doc_id and name:
            documents.append({'doc_id': doc_id, 'name': name,
                              'status': (item.get('Lifecycle') or {}).get('Status')})
    return documents


def existing_document_names(kb_id, region):
    """知识库里已有的文档名集合（用于上传前判重，省一次白传）。"""
    return {item['name'] for item in describe_documents(kb_id, region)}


def push_document(content, filename, tag=DEFAULT_TAG, chunk_length=DEFAULT_CHUNK_LENGTH,
                  overlap=DEFAULT_OVERLAP, enable_scope=4):
    """把一份已经切好的 Markdown 送进知识库（本地网站在确认资料后调用）。

    只返回状态、不抛异常：自动上传失败不能影响本地网站保存资料。
    上传前先查同名，避免白传一次（平台按文件名去重）。
    返回 {'status': 'uploaded'|'duplicate'|'skipped'|'failed', 'doc_id': '', 'message': ''}。
    """
    values = settings()
    if values['ADP_AUTO_UPLOAD'].strip() == '0':
        return {'status': 'skipped', 'doc_id': '', 'message': 'ADP_AUTO_UPLOAD=0，已关闭自动上传'}
    kb_id = values['ADP_KB_ID']
    missing = [name for name in ('TENCENT_SECRET_ID', 'TENCENT_SECRET_KEY', 'ADP_KB_ID')
               if not values[name]]
    if missing:
        return {'status': 'skipped', 'doc_id': '', 'message': '缺少配置：' + '、'.join(missing)}
    region = values['ADP_REGION'] or 'ap-guangzhou'
    file_type = Path(filename).suffix.lstrip('.').lower() or 'md'
    try:
        if filename in existing_document_names(kb_id, region):
            doc_id = next((d['doc_id'] for d in describe_documents(kb_id,region) if d['name']==filename),'')
            return {'status': 'duplicate', 'doc_id': doc_id,
                    'message': '知识库里已有同名文档，跳过上传。'}
    except ValueError:
        pass  # 查不到就继续走正常上传，由平台去重兜底
    try:
        credential = api_call('DescribeStorageCredential',
                              {'BotBizId': kb_id, 'FileType': file_type,
                               'TypeKey': 'offline', 'IsPublic': False},
                              region, values['TENCENT_SECRET_ID'], values['TENCENT_SECRET_KEY'],
                              DEFAULT_API)
        _, upload_path = credential_parts(credential)
        if not upload_path:
            raise ValueError('平台没有返回 UploadPath')
        etag, crc64, size = upload_to_cos(credential, content, upload_path)
        payload = save_payload(filename, upload_path, etag, crc64, size, kb_id, enable_scope,
                               split_rule(tag, chunk_length, overlap), 'BotBizId')
        result = api_call('SaveDoc', payload, region, values['TENCENT_SECRET_ID'],
                          values['TENCENT_SECRET_KEY'], DEFAULT_API)
    except ValueError as exc:
        message = str(exc)
        if '重复文档名称' in message or '450020' in message:
            match = re.search(r'文档ID[:：]\s*(\d+)', message)
            return {'status': 'duplicate', 'doc_id': match.group(1) if match else '',
                    'message': '知识库里已有同名文档，没有重复导入。'}
        return {'status': 'failed', 'doc_id': '', 'message': message[:300]}
    return {'status': 'uploaded', 'doc_id': str(result.get('DocBizId') or ''), 'message': ''}


def set_document_domain(doc_id, domain):
    values = settings()
    return api_call('ModifyDoc', {'KbId':values['ADP_KB_ID'], 'DocId':str(doc_id),
        'Fields':{'EffectiveDomain':int(domain)}, 'UpdateMask':{'Paths':['EffectiveDomain']}},
        values['ADP_REGION'] or 'ap-guangzhou',values['TENCENT_SECRET_ID'],values['TENCENT_SECRET_KEY'],'new')


def document_state(doc_id):
    values = settings()
    result = api_call('DescribeDoc',{'KbId':values['ADP_KB_ID'],'DocId':str(doc_id)},
        values['ADP_REGION'] or 'ap-guangzhou',values['TENCENT_SECRET_ID'],values['TENCENT_SECRET_KEY'],'new')
    summary = result.get('Summary') or {}
    return {'status': (summary.get('Lifecycle') or {}).get('Status'),
            'description': (summary.get('Lifecycle') or {}).get('StatusDesc', ''),
            'domain': (summary.get('KnowledgeScope') or {}).get('EffectiveDomain')}


def wait_document_ready(doc_id, domain=None, timeout=180):
    deadline = time.monotonic() + timeout
    delay = 2
    while True:
        state = document_state(doc_id)
        if state['status'] == 8 and (domain is None or state['domain'] == domain):
            return state
        if state['status'] == 7 or '失败' in state['description']:
            raise ValueError('知识库文档处理失败：' + str(state['description'] or state['status']))
        if time.monotonic() >= deadline:
            raise ValueError('知识库处理或生效范围确认超时；本地更新尚未生效，可重试。')
        time.sleep(min(delay,max(0,deadline-time.monotonic())))
        delay = min(delay*2,15)


def check_configuration(config, kb_id, region, api):
    """先做本地格式自检，再联网验证，给出能照着改的诊断。"""
    secret_id, secret_key = config['TENCENT_SECRET_ID'], config['TENCENT_SECRET_KEY']
    problems = []
    if not secret_id:
        problems.append('TENCENT_SECRET_ID 为空：到腾讯云控制台 → 访问管理 → 访问密钥 → API 密钥管理，用“复制”取值')
    elif not secret_id.startswith('AKID'):
        problems.append(f'TENCENT_SECRET_ID 必须“AKID”开头，现在是“{secret_id[:8]}…”（这把不是腾讯云密钥）')
    elif len(secret_id) != 36:
        problems.append(f'TENCENT_SECRET_ID 应为 36 位，现在是 {len(secret_id)} 位')
    if len(secret_key) != 32:
        problems.append(f'TENCENT_SECRET_KEY 应为 32 位，现在是 {len(secret_key)} 位')
    if not kb_id:
        problems.append('ADP_KB_ID 为空：填慧科杯知识库的 ID')
    for item in problems:
        print('✗ ' + item)
    if problems:
        return 1
    try:
        credential = api_call('DescribeStorageCredential',
                              {'BotBizId': kb_id, 'FileType': 'md',
                               'TypeKey': 'offline', 'IsPublic': False},
                              region, secret_id, secret_key, api)
    except ValueError as exc:
        print(f'✗ 联网验证失败：{exc}')
        print('  提示：BotBizId 要填知识库 ID（应用与默认知识库共用同一个 ID）；'
              '可用 --api new 调 DescribeKBSummaryList 查（需要 SpaceId=default_space）。')
        return 1
    _, upload_path = credential_parts(credential)
    if not upload_path:
        print('✗ 平台没有返回可写的 UploadPath，请检查知识库 ID 是否正确。')
        return 1
    print(f'✓ 密钥与知识库都可用；平台分配的测试上传路径：{upload_path}')
    print('  下一步：python adp.py --all（把 docs/adp/data 下所有上传稿送进知识库）')
    return 0


def list_documents(kb_id, region):
    """列出知识库里的文档（用新版 DescribeDocSummaryList，与 --api 无关）。"""
    values = settings()
    result = api_call('DescribeDocSummaryList', {'KbId': kb_id}, region,
                      values['TENCENT_SECRET_ID'], values['TENCENT_SECRET_KEY'], 'new')
    items = result.get('DocList', [])
    if not items:
        print('知识库暂无文档。')
        return 0
    for item in items:
        metadata = item.get('Metadata') or {}
        lifecycle = item.get('Lifecycle') or {}
        print(f"  {item.get('DocId')}｜{metadata.get('FileName')}"
              f"｜{lifecycle.get('StatusDesc')}｜{metadata.get('DocCharCount')} 字")
    return 0


def delete_documents(kb_id, region, doc_ids):
    """按文档 ID 删除知识库文档（新版 DeleteDocList，ID 用 --list 查）。"""
    values = settings()
    result = api_call('DeleteDocList', {'KbId': kb_id, 'DocIdList': list(doc_ids)}, region,
                      values['TENCENT_SECRET_ID'], values['TENCENT_SECRET_KEY'], 'new')
    for item in result.get('ResultList', []):
        state = '已删除' if item.get('Succeeded') else f"失败：{item.get('Reason')}"
        print(f"  {item.get('Id')}｜{state}")
    return 0


def verify(question, limit=3):
    """用 AppKey 提问，看引用来自哪些文件：用来确认资料真的进了知识库。"""
    import eval_adp
    values = eval_adp.settings()
    if not values['ADP_APP_KEY'] or not values['ADP_CHAT_URL']:
        print('缺少 ADP_APP_KEY / ADP_CHAT_URL，无法验证。')
        return 1
    parsed, _ = eval_adp.call(values['ADP_CHAT_URL'], values['ADP_APP_KEY'], question)
    print('问题：' + question)
    print('回答：' + (parsed.get('answer') or '（未解析出文本）'))
    if parsed.get('errors'):
        print('错误事件：' + '；'.join(parsed['errors']))
        return 1
    quotes = parsed.get('quotes') or []
    if not quotes:
        print('引用：无 → 说明这个问题没有命中知识库内容')
        return 1
    for item in quotes[:limit]:
        print(f"  引用 [{item['index']}] {item['name']}｜{item['url']}")
    return 0


def main():
    parser = argparse.ArgumentParser(description='把分块文本送进 ADP 知识库（SaveDoc 三步）。')
    parser.add_argument('--file', nargs='+', help='要上传的 .md/.txt 文件，可给多个')
    parser.add_argument('--all', action='store_true', help='提交 docs/adp/data 下所有“知识库上传”稿件')
    parser.add_argument('--name', help='知识库里的文件名；只在只传一个文件时生效')
    parser.add_argument('--check', action='store_true', help='只检查密钥与知识库配置，不上传')
    parser.add_argument('--verify', metavar='问题', help='用 AppKey 提问，检查资料是否已在知识库里')
    parser.add_argument('--kb-id', help='知识库 ID；不填则读 .env 的 ADP_KB_ID')
    parser.add_argument('--region', help='地域；不填则读 .env 的 ADP_REGION，默认 ap-guangzhou')
    parser.add_argument('--tag', default=DEFAULT_TAG, help='切分标识符，默认 ---')
    parser.add_argument('--chunk-length', type=int, default=DEFAULT_CHUNK_LENGTH, help='切片最大长度')
    parser.add_argument('--overlap', type=int, default=DEFAULT_OVERLAP, help='切片重叠长度')
    parser.add_argument('--enable-scope', type=int, default=4,
                        help='生效范围：1 不生效 / 2 仅开发域 / 3 仅发布域 / 4 都生效')
    parser.add_argument('--api', choices=sorted(API_MODES), default=DEFAULT_API,
                        help='new = adp.tencentcloudapi.com/2026-05-20；old = lke.tencentcloudapi.com/2023-11-30')
    parser.add_argument('--kb-field', default='',
                        help='知识库 ID 的参数名；留空按接口默认（新版 KbId / 旧版 BotBizId）')
    parser.add_argument('--type-key', default='offline',
                        help='TypeKey：offline=离线文档（默认）／realtime=实时对话文件')
    parser.add_argument('--public', action='store_true',
                        help='按“公有场景”取路径；离线文档传 false，不要加这个参数')
    parser.add_argument('--list', action='store_true', help='列出知识库现有文档')
    parser.add_argument('--delete', nargs='+', metavar='DOCID',
                        help='删除知识库文档（ID 用 --list 查）')
    parser.add_argument('--dry-run', action='store_true', help='只打印将要发送的内容，不联网')
    args = parser.parse_args()

    config = settings()
    kb_id = args.kb_id or config['ADP_KB_ID']
    region = args.region or config['ADP_REGION'] or 'ap-guangzhou'
    rule = split_rule(args.tag, args.chunk_length, args.overlap)
    host, _, version = API_MODES[args.api]
    kb_field = args.kb_field or ('KbId' if args.api == 'new' else 'BotBizId')

    if args.list:
        return list_documents(kb_id, region)

    if args.delete:
        return delete_documents(kb_id, region, args.delete)

    if args.verify:
        return verify(args.verify)

    print(f'接口：{host}（{version}）｜知识库 ID 字段名：{kb_field}')
    print(f'知识库 ID：{kb_id or "（未配置）"}｜地域：{region}｜生效范围：{args.enable_scope}')
    print(f'切分规则：{rule}')

    if args.check:
        return check_configuration(config, kb_id, region, args.api)

    if args.all:
        sources = sorted(UPLOAD_DIR.glob('*知识库上传*.md'))
        if not sources:
            print(f'没有找到可提交的稿件：{UPLOAD_DIR}')
            return 1
    else:
        sources = [Path(item) for item in (args.file or [])]
        if not sources:
            print('请用 --file 指定稿件，或加 --all 提交 docs/adp/data 下的全部上传稿。')
            return 1
    for source in sources:
        if not source.exists():
            print(f'找不到文件：{source}')
            return 1
    filename_of = lambda source: args.name if args.name and len(sources) == 1 else source.name

    if args.dry_run:
        print('三步：DescribeStorageCredential → COS PUT → SaveDoc')
        for source in sources:
            plan = save_payload(filename_of(source), '<DescribeStorageCredential 的上传路径>', '<ETag>', '<CRC64>',
                                source.stat().st_size, kb_id or '<ADP_KB_ID>', args.enable_scope, rule, kb_field)
            print(f'将要发送的 SaveDoc 请求体（{source.name}）：')
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    missing = missing_settings()
    if missing:
        print('缺少配置：' + '、'.join(missing) + '（写到 website/.env 或环境变量里）')
        print('先跑 `python adp.py --check`，它会说明每个字段该怎么取。')
        return 1

    failed = 0
    for source in sources:
        print(f'文件：{source.name}（{source.stat().st_size} 字节）')
        try:
            submit(source, filename_of(source), args, config, kb_id, region, kb_field, rule)
        except ValueError as exc:
            print(f'  入库失败：{exc}')
            if 'SecretIdNotFound' in str(exc):
                print('  提示：SecretId 应是“AKID”开头的 36 位字符串'
                      '（腾讯云控制台 → 访问管理 → 访问密钥 → API 密钥管理，用“复制”取值）。')
            failed += 1
        except KeyError as exc:
            print(f'  入库失败：返回里缺少字段 {exc}。')
            failed += 1
    print(f'提交结束：成功 {len(sources) - failed} 个，失败 {failed} 个。')
    if not failed:
        print('接着到知识库页面确认状态为“导入完成”，'
              '再用 `python adp.py --verify "一个问题"` 验证引用来源。')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
