import os
import sys
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown

# 添加项目根目录到 sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.graph import build_graph
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

console = Console()

def init():
    """初始化环境与配置"""
    load_dotenv()
    console.print("[bold green]🤖 Agent Runtime 初始化完成...[/bold green]")

def run_interactive():
    init()
    graph = build_graph()
    
    console.print(Markdown("# 欢迎使用 Code Agent (LangGraph 版)"))
    console.print("系统支持: Multi-Model, Tools, Skills & Memory. 输入 'exit' 退出。\n")

    # 定义一个会话配置，LangGraph 依据它来追踪上下文记忆
    config = {"configurable": {"thread_id": "session_001"}}

    while True:
        try:
            user_input = console.input("[bold blue]You>[/bold blue] ")
            if user_input.lower() in ['exit', 'quit']:
                break
            if not user_input.strip():
                continue

            console.print("[dim]运行图中...[/dim]")
            
            # 记录本次请求的累计 Token 消耗
            run_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            tool_args_length = 0

            # 启用 stream_mode="messages" 使得 LLM 可以打字机般输出
            for msg, metadata in graph.stream({"messages": [HumanMessage(content=user_input)]}, config, stream_mode="messages"):
                
                # 1. 打印普通的 AI 文本回复
                if msg.content and isinstance(msg, AIMessage):
                    print(msg.content, end="", flush=True)

                # 2. 捕获工具调用的流式输出 (防止生成代码等长任务时觉得卡死)
                if hasattr(msg, "tool_call_chunks") and msg.tool_call_chunks:
                    for tc_chunk in msg.tool_call_chunks:
                        # 刚开始调用某个工具时，打印工具名
                        if tc_chunk.get("name"):
                            console.print(f"\n\n[bold yellow]⚙️ 正在执行工具[/bold yellow]: [bold cyan]{tc_chunk['name']}[/bold cyan]")
                            tool_args_length = 0
                        
                        # 动态更新字数而不再打印大块参数或点点点
                        if tc_chunk.get("args"):
                            tool_args_length += len(tc_chunk["args"])
                            # \r 回到行首，\033[K 清除该行，实现原地刷新动画效果
                            print(f"\r\033[K\033[90m⏳ 正在生成工具参数 (已加载 {tool_args_length} 个字符)...\033[0m", end="", flush=True)

                # 3. 累计 Token 消耗并实时打印
                if hasattr(msg, 'usage_metadata') and msg.usage_metadata is not None:
                    run_usage["input_tokens"] += msg.usage_metadata.get("input_tokens", 0)
                    run_usage["output_tokens"] += msg.usage_metadata.get("output_tokens", 0)
                    run_usage["total_tokens"] += msg.usage_metadata.get("total_tokens", 0)
                    console.print(f"\n\n[dim]💰 阶段 Token 结算: In {msg.usage_metadata.get('input_tokens')} | Out {msg.usage_metadata.get('output_tokens')}[/dim]")
                
                # 4. 打印工具实际执行完成后的返回结果
                if isinstance(msg, ToolMessage):
                    print("\r\033[K\n", end="") # 清除最后一次的 generating 状态并换行
                    console.print(f"[bold green]✔️ 工具执行完毕[/bold green]: {msg.name} -> {str(msg.content)[:150]}...") # 截断结果避免刷屏

            # 换行并汇总本轮 Token 开销
            print("\n")
            console.print(f"[dim]📊 本轮消耗: 输入 {run_usage['input_tokens']} Tokens | 输出 {run_usage['output_tokens']} Tokens | 总计 {run_usage['total_tokens']} Tokens[/dim]\n")

        except KeyboardInterrupt:
            console.print("\n[yellow]检测到终端中断，退出系统。[/yellow]")
            break
        except Exception as e:
            console.print(f"[bold red]运行时错误: {e}[/bold red]")

if __name__ == "__main__":
    run_interactive()
