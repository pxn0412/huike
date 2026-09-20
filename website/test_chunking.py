import unittest

import chunking


EXPORT = '''# 测试通知

- 比赛：测试比赛（2026）
- 来源文件：test.pdf

## 第 1 页（OCR 识别）

一、大赛宗旨

本次大赛设置双平行赛道。

## 第 2 页（OCR 识别）

（扫描二维码填写报名信息）
'''


LONG_BODY = '本次大赛设置两条平行赛道，各赛道独立评审、独立评奖，参赛作品须立足真实场景解决实际问题。'


class ChunkingTests(unittest.TestCase):
    def test_normalize_removes_ocr_spaces_between_chinese(self):
        self.assertEqual(chunking.normalize('大赛 宗旨 、 组织架构'), '大赛宗旨、组织架构')
        self.assertEqual(chunking.normalize('PBL 项目'), 'PBL 项目')  # 英文与数字两侧不动

    def test_noise_and_parenthetical_are_not_headings(self):
        pages = [(1, '一、参赛方式\n（扫描二维码填写报名信息）\n0 到 0 0 的 0\n到 苤 0 0')]
        blocks = chunking.build_blocks(pages, min_chars=0)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['heading'], '一、参赛方式')
        self.assertNotIn('苤', blocks[0]['text'])

    def test_numbered_sections_split_and_keep_wording(self):
        pages = [(1, f'一、大赛宗旨\n{LONG_BODY}\n二、组织架构\n{LONG_BODY}')]
        blocks = chunking.build_blocks(pages, min_chars=0)
        self.assertEqual([b['heading'] for b in blocks], ['一、大赛宗旨', '二、组织架构'])
        self.assertEqual(blocks[0]['text'], f'一、大赛宗旨\n{LONG_BODY}')
        self.assertEqual(blocks[0]['chunk_id'], 'N01')

    def test_isolated_short_line_becomes_a_heading(self):
        pages = [(1, '有关事项通知如下：\n大赛宗旨\n本次大赛设置两条平行赛道。')]
        blocks = chunking.build_blocks(pages, min_chars=0)
        self.assertEqual([b['heading'] for b in blocks], ['', '大赛宗旨'])

    def test_wrapped_fragment_is_not_a_heading(self):
        # OCR 把一句话的最后两个字折到单独一行时，不能当成小节标题
        pages = [(1, '一、大赛宗旨\n公示复赛晋级的完整名单如下\n名单\n二、组织架构\n正文')]
        blocks = chunking.build_blocks(pages, min_chars=0)
        self.assertEqual([b['heading'] for b in blocks], ['一、大赛宗旨', '二、组织架构'])
        self.assertIn('名单', blocks[0]['text'])

    def test_short_title_rule_has_escape_hatch(self):
        pages = [(1, '有关事项通知如下：\n大赛宗旨\n本次大赛设置两条平行赛道。')]
        self.assertEqual(len(chunking.build_blocks(pages, min_chars=0)), 2)
        self.assertEqual(len(chunking.build_blocks(pages, min_chars=0, short_titles=False)), 1)

    def test_same_input_gives_same_output(self):
        pages = [(1, f'一、大赛宗旨\n{LONG_BODY}\n二、组织架构\n{LONG_BODY}')]
        first = chunking.render_markdown(chunking.build_blocks(pages), 'T', [])
        second = chunking.render_markdown(chunking.build_blocks(pages), 'T', [])
        self.assertEqual(first, second)

    def test_track_labels_from_keywords(self):
        self.assertEqual(chunking.track_of('赛道二：AI 智能体应用赛', ''), 'AI')
        self.assertEqual(chunking.track_of('赛道一：PBL 项目开发赛', ''), 'PBL')
        self.assertEqual(chunking.track_of('八、评审标准 · （一）PB 凵页目开发赛', ''), 'PBL')
        # 标题是中性的、只有正文提到平台 → 不标赛道，宁可标通用
        self.assertEqual(chunking.track_of('、组织架构', '技术支持：腾讯云智能体开发平台'), None)
        # 同一句里同时出现两条赛道 → 判为通用
        self.assertEqual(chunking.track_of('', '本次大赛设置 PBL 项目开发、AI 智能体应用双平行赛道'), None)
        # OCR 把 “AI” 认成 “A 丨”，材料清单同时列两条赛道时仍要判为通用
        self.assertEqual(chunking.track_of('（二）初赛作品提交',
                                           '1. PBL 项目开发赛：项目设计 PPT；2. A 丨智能体应用赛：方案 PPT'), None)

    def test_parent_heading_merges_into_child_section(self):
        pages = [(1, '八、评审标准\n本次大赛各赛道满分均为 100 分，评分标准如下：\n'
                     '（一）PBL 项目开发赛\n1. 技术创新性（25 分）：作品技术思路具备创新亮点。')]
        blocks = chunking.build_blocks(pages, min_chars=0)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['heading'], '八、评审标准 · （一）PBL 项目开发赛')
        self.assertIn('满分均为 100 分', blocks[0]['text'])
        self.assertIn('技术创新性', blocks[0]['text'])

    def test_sibling_sections_stay_separate(self):
        pages = [(1, f'三、参赛对象\n浙江广厦建设职业技术大学全体在校生。\n四、大赛时间安排\n{LONG_BODY}')]
        blocks = chunking.build_blocks(pages, min_chars=0)
        self.assertEqual([b['heading'] for b in blocks], ['三、参赛对象', '四、大赛时间安排'])

    def test_cross_page_continuation_merges_with_overlap(self):
        pages = [(1, '四、大赛时间安排\n1．报名：即日起'), (2, '2．提交：6月24日')]
        blocks = chunking.build_blocks(pages, min_chars=0)
        self.assertEqual(len(blocks), 1)  # 跨页续写并进上一块，不新开块
        self.assertEqual(blocks[0]['page_end'], 2)
        self.assertIn('（接上页）1．报名：即日起', blocks[0]['text'])
        self.assertNotIn('（接上页）四、大赛时间安排', blocks[0]['text'])

    def test_render_markdown_has_block_heading_and_separator(self):
        pages = [(1, f'一、大赛宗旨\n{LONG_BODY}'), (2, f'赛道二：AI 智能体应用赛\n{LONG_BODY}')]
        blocks = chunking.build_blocks(pages, min_chars=0)
        markdown = chunking.render_markdown(blocks, '测试通知', [('比赛', '测试比赛（2026）')])
        self.assertIn('## 块 N01 ｜一、大赛宗旨（第 1 页，两赛道通用）', markdown)
        self.assertIn('## 块 N02 ｜赛道二：AI 智能体应用赛（第 2 页，仅 AI 智能体应用赛）', markdown)
        self.assertEqual(markdown.count('\n---\n'), 3)  # 文件头 1 次 + 每块 1 次

    def test_render_yaml_keeps_text_as_block_scalar(self):
        pages = [(1, '一、大赛宗旨\n正文甲')]
        blocks = chunking.build_blocks(pages, min_chars=0)
        yaml = chunking.render_yaml(blocks, 'huike-2026-notice')
        self.assertIn('  - chunk_id: N01', yaml)
        self.assertIn('    document_id: huike-2026-notice', yaml)
        self.assertIn('    text: |', yaml)
        self.assertIn('      正文甲', yaml)

    def test_read_markdown_pages(self):
        pages = chunking.read_markdown_pages(EXPORT)
        self.assertEqual([number for number, _ in pages], [1, 2])
        self.assertIn('一、大赛宗旨', pages[0][1])


if __name__ == '__main__':
    unittest.main()
