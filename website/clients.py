"""外部平台边界：知识库与已发布智能体。

第一批只落这两条边界，**不落 JobRunner / FileStorage**（等真做异步任务和换存储时再抽，
避免为了“以后可能要”扩大现在的迁移与测试范围）。

上层的 store.py / server.py 只认这两个门面，不再直接 import adp / agent；
以后要换对象存储、换平台版本（旧版 lke ↔ 新版 adp）、换异步执行器，都只改这里。
"""

import adp
import agent

# 平台说“这个会话不能用了”时抛这个异常：上层换一个新会话重试一次即可。
SessionExpired = agent.SessionExpired


class KnowledgeBaseClient:
    """知识库写入侧。上传走旧版 lke（DescribeStorageCredential → COS → SaveDoc）。"""

    @staticmethod
    def configured():
        """密钥与知识库 ID 是否齐全（不联网）。"""
        return not adp.missing_settings()

    @staticmethod
    def push(content, filename):
        """把切好的 Markdown 送进知识库。

        只返回状态、不抛异常；返回 {'status', 'doc_id', 'message'}，
        status 取 uploaded / duplicate / skipped / failed。
        """
        return adp.push_document(content, filename)

    @staticmethod
    def document_names(kb_id, region):
        """知识库里已有的文档名（上传前判重，省一次白传）。"""
        return adp.existing_document_names(kb_id, region)

    @staticmethod
    def existing_documents():
        """知识库里现有的文档（含平台文档 ID），用于把老资料认领回本地。

        没配好密钥就抛 ValueError，由调用方决定要不要提示。
        """
        values = adp.settings()
        missing = [name for name in ('TENCENT_SECRET_ID', 'TENCENT_SECRET_KEY', 'ADP_KB_ID')
                   if not values[name]]
        if missing:
            raise ValueError('缺少配置：' + '、'.join(missing))
        return adp.describe_documents(values['ADP_KB_ID'], values['ADP_REGION'] or 'ap-guangzhou')


class AgentClient:
    """已发布智能体的问答侧（HTTP SSE，只需要 AppKey）。"""

    @staticmethod
    def configured():
        return agent.configured()

    @staticmethod
    def ask(question, competition=None, conversation_id=None):
        """问一次智能体；平台报错或没有正文时抛 ValueError，由调用方提示用户。

        带 conversation_id 就是追问（同一个用户在同一场比赛里接着上文问）；
        不传就开新会话。会话失效抛 SessionExpired，由调用方换新会话重试一次。
        """
        return agent.ask(question, competition=competition, conversation_id=conversation_id)


class ProfileAgentClient:
    """学生画像智能体（第二个应用）。

    它不读比赛知识库，所以不问比赛、不注入"当前比赛"，也不做引用范围校验；
    "能不能参赛"这类问题由它转给资料问答那条线（写进它的提示词里）。
    """

    @staticmethod
    def configured():
        return agent.profile_configured()

    @staticmethod
    def ask(question, conversation_id=None):
        return agent.ask_profile(question, conversation_id=conversation_id)


knowledge_base = KnowledgeBaseClient()
agent_client = AgentClient()
profile_agent = ProfileAgentClient()
