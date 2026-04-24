import os
from langchain_openai import ChatOpenAI

def get_model(model_type="chat"):
    """
    模型工厂类，统一封装兼容 OpenAI 协议的模型 API。
    通过环境变量加载对应服务的地址和秘钥。
    """
    if model_type == "deepseek-chat":
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key or api_key == "your_deepseek_api_key_here":
            raise ValueError("请在 .env 文件中配置 DEEPSEEK_API_KEY")
            
        return ChatOpenAI(
            api_key=api_key, 
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            streaming=True,
        )
    elif model_type == "deepseek-reasoner":
        # 意图分析使用 deepseek-reasoner
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key or api_key == "your_deepseek_api_key_here":
            raise ValueError("请在 .env 文件中配置 DEEPSEEK_API_KEY")
            
        return ChatOpenAI(
            api_key=api_key, 
            base_url="https://api.deepseek.com",
            model="deepseek-reasoner",
            streaming=True,
        )
    elif model_type == "doubao":
        # 使用豆包2.0code (注意：火山引擎通常需要填写真实的 endpoint id 作为 model 参数，这里填入占位符，运行时若报错请替换为您新建的 Endpoint 接入点)
        api_key = os.getenv("DOUBAO_API_KEY", "")
        return ChatOpenAI(
            api_key=api_key, 
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            model="doubao-seed-2-0-code-preview-260215",
            streaming=True, 
        )
    elif model_type == "glm":
        # chat使用智谱 GLM
        api_key = os.getenv("ZHIPU_API_KEY", "")
        return ChatOpenAI(
            api_key=api_key,
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4-flash", # 或者配置为您想要的 glm-4 模型
            streaming=True,
        )
    else:
        raise ValueError(f"未知的模型类型: {model_type}")
