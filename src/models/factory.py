import os
from langchain_openai import ChatOpenAI

def get_model(model_type="chat"):
    """
    模型工厂类，统一封装兼容 OpenAI 协议的模型 API。
    通过环境变量加载对应服务的地址和秘钥。
    """
    if model_type == "chat":
        # 默认使用 DeepSeek 的对话模型
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key or api_key == "your_deepseek_api_key_here":
            raise ValueError("请在 .env 文件中配置 DEEPSEEK_API_KEY")
            
        return ChatOpenAI(
            api_key=api_key, 
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            streaming=True,
        )
    elif model_type == "reasoner":
        # 使用 DeepSeek 的推理引擎 R1/Reasoner
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        return ChatOpenAI(
            api_key=api_key, 
            base_url="https://api.deepseek.com",
        )
    elif model_type == "glm":
        # 兼容智谱 GLM-4
        api_key = os.getenv("ZHIPU_API_KEY", "")
        return ChatOpenAI(
            api_key=api_key,
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4-flash",
            streaming=True,
        )
    else:
        raise ValueError(f"未知的模型类型: {model_type}")
