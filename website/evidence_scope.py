"""Conservative competition-track scope for stored source excerpts."""
import re
import unicodedata


COMMON = {'', '全比赛', '全部赛道', '所有赛道', '通用', '未确认', '暂不确定',
          '赛道范围未单独标注'}
KNOWN_TRACKS = {
    'AI': ('AI 智能体应用赛', re.compile(r'AI\s*智能体\s*应用赛', re.I)),
    'PBL': ('PBL 项目开发赛', re.compile(r'PBL\s*项目开发赛', re.I)),
}


def named_tracks(text):
    return {name for name, (_, pattern) in KNOWN_TRACKS.items() if pattern.search(text or '')}


def compact(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return ''.join(char for char in value if char.isalnum())


def key(value):
    value = str(value or '').strip()
    if value in COMMON:
        return ''
    normalized = compact(re.sub(r'^仅\s*', '', value))
    if normalized.startswith('ai') and ('智能体' in normalized or '智能应用' in normalized):
        return 'ai-agent'
    if normalized.startswith('pbl') and '项目开发' in normalized:
        return 'pbl'
    return normalized


def block_track(heading, text, document_track=''):
    """Only an explicit track name in a heading scopes a mixed notice excerpt."""
    if key(document_track):
        return document_track
    source = f'{heading or ""}\n{text or ""}'
    found = [label for label, pattern in KNOWN_TRACKS.values()
             if pattern.search(source)]
    return found[0] if len(found) == 1 else ('多赛道混合，待核对' if found else '')


def question_track(question, available):
    """Return one clearly named track; ambiguous or general questions keep all."""
    query = compact(question)
    # The requested scope comes from the question, not from whatever documents
    # happen to be indexed. Otherwise an empty AI track could expose PBL text.
    explicit = set()
    if '智能体' in query:
        explicit.add('ai-agent')
    if 'pbl' in query:
        explicit.add('pbl')
    if len(explicit) > 1:
        return None
    if explicit:
        return next(iter(explicit))
    found = []
    for label in available:
        canonical = key(label)
        if not canonical:
            continue
        normalized = compact(re.sub(r'^仅\s*', '', str(label)))
        # Full name, distinctive Chinese fragment, or an acronym explicitly called a track.
        chinese = re.findall(r'[\u4e00-\u9fff]{3,}', normalized)
        matched = normalized in query or any(any(part[i:i+3] in query
                       for i in range(len(part)-2)) for part in chinese)
        acronym = re.match(r'[a-z]{2,}', normalized)
        if acronym and (acronym.group() != 'ai' or '赛道' in question):
            matched = matched or acronym.group() in query
        if matched:
            found.append(canonical)
    return found[0] if len(set(found)) == 1 else None


def permits(chunk_track, requested_track):
    return not requested_track or not key(chunk_track) or key(chunk_track) == requested_track
