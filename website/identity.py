"""匿名会话身份：由服务端签发，只用来判断“这份资料是不是同一个人传的”。

刻意不做认证：
    学生 / 教师 = 用户自己选的展示身份，只写在资料上，不参与任何权限判断；
    上传者姓名与身份来自表单（可随便填），归属判断**只认这里的 Cookie**，
    前端传来的一律不采信（见 server.py 里 inject_owner）。

Cookie 是 HttpOnly，前端 JS 读不到，换浏览器或清 Cookie 就会失去撤销权 ——
这是本 Demo 明确的取舍，写在 docs/adp 的交接文档里。
"""
import re
import secrets

COOKIE_NAME = 'huike_owner'
COOKIE_MAX_AGE = 60 * 60 * 24 * 180
OWNER_PATTERN = re.compile(r'^[a-f0-9]{32}$')


def issue():
    """签发一个新的匿名会话 ID（32 位十六进制，和资料 ID 同格式）。"""
    return secrets.token_hex(16)


def from_header(cookie_header):
    """从 Cookie 头里取出会话 ID；缺失或格式不对都当没有。"""
    for part in str(cookie_header or '').split(';'):
        name, _, value = part.strip().partition('=')
        if name == COOKIE_NAME:
            value = value.strip()
            return value if OWNER_PATTERN.fullmatch(value) else None
    return None


def ensure(cookie_header):
    """返回 (会话 ID, 是否要下发新 Cookie)。首次访问在这里发一个。"""
    current = from_header(cookie_header)
    return (current, False) if current else (issue(), True)


def cookie_header(owner):
    """Set-Cookie 的值。SameSite=Strict 防跨站带 Cookie，HttpOnly 防 JS 读取。"""
    return (f'{COOKIE_NAME}={owner}; Path=/; HttpOnly; SameSite=Strict; '
            f'Max-Age={COOKIE_MAX_AGE}')


def clear_cookie_header():
    """退出登录：立刻作废这个浏览器的身份 Cookie（下次访问重新签发匿名身份）。"""
    return f'{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'
