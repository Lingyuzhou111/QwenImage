import os
import re
import json
import requests
import time
from typing import Tuple

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import *

@plugins.register(
    name="QwenImage",
    desire_priority=80,
    hidden=False,
    desc="A plugin for generating images using Qwen Image API via DashScope.",
    version="1.0.0",
    author="Assistant",
)
class QwenImage(Plugin):
    def __init__(self):
        super().__init__()
        try:
            conf = super().load_config()
            if not conf:
                raise Exception("配置未找到。")

            # 读取Qwen Image配置
            qwen_config = conf.get("qwen_image", {})
            if not qwen_config:
                raise Exception("在配置中未找到qwen_image配置。")

            self.base_url = qwen_config.get("base_url", "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis")
            self.models = qwen_config.get("model", ["wan2.2-t2i-flash", "wan2.2-t2i-plus"])
            self.default_model = self.models[0] if self.models else "wan2.2-t2i-flash"
            
            # API密钥配置
            self.api_key_1 = qwen_config.get("api_key_1", "")
            self.api_key_2 = qwen_config.get("api_key_2", "")
            self.current_api_key = self.api_key_1  # 默认使用第一个API密钥
            self.current_account = 1  # 当前使用的账号编号
            
            # 绘图命令前缀
            self.drawing_prefixes = conf.get("image_command", ["Q画图", "Q生成"])
            
            # 控制命令前缀
            self.control_prefixes = conf.get("control_command", ["Q开启智能扩写", "Q禁用智能扩写"])
            
            # 账号切换命令前缀
            self.account_prefixes = conf.get("account_command", ["Q切换账号 1", "Q切换账号 2"])
            
            # 图片比例配置
            self.ratios = qwen_config.get("ratios", {
                "1:1": {"width": 1024, "height": 1024},
                "2:3": {"width": 896, "height": 1344},
                "3:4": {"width": 960, "height": 1280},
                "4:3": {"width": 1280, "height": 960},
                "3:2": {"width": 1344, "height": 896},
                "16:9": {"width": 1344, "height": 768},
                "9:16": {"width": 768, "height": 1344}
            })
            self.default_ratio = qwen_config.get("default_ratio", "1:1")
            
            # 用户状态管理（用于存储每个用户的智能扩写设置）
            self.user_prompt_extend_settings = {}  # 用户ID -> 智能扩写设置
            self.global_prompt_extend = True  # 全局默认智能扩写设置

            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

            logger.info(f"[QwenImage] 初始化成功，可用模型: {self.models}")
        except Exception as e:
            logger.error(f"[QwenImage] 初始化失败，错误：{e}")
            raise e

    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type != ContextType.TEXT:
            return

        content = e_context["context"].content
        
        # 检查是否是绘图命令
        if content.startswith(tuple(self.drawing_prefixes)):
            self.handle_drawing_command(e_context)
        # 检查是否是控制命令
        elif content.startswith(tuple(self.control_prefixes)):
            self.handle_control_command(e_context)
        # 检查是否是账号切换命令
        elif content.startswith(tuple(self.account_prefixes)):
            self.handle_account_command(e_context)
        else:
            return

    def handle_drawing_command(self, e_context: EventContext):
        """处理绘图命令"""
        content = e_context["context"].content
        logger.debug(f"[QwenImage] 收到绘图消息: {content}")

        try:
            # 移除前缀
            used_prefix = None
            for prefix in self.drawing_prefixes:
                if content.startswith(prefix):
                    content = content[len(prefix):].strip()
                    used_prefix = prefix
                    break

            # 解析用户输入
            prompt_text, image_size, model, prompt_extend = self.parse_user_input(content, e_context["context"])
            logger.debug(f"[QwenImage] 解析后的参数: 提示词={prompt_text}, 尺寸={image_size}, 模型={model}")

            if not prompt_text:
                reply = Reply(ReplyType.TEXT, "请输入需要生成的图片描述")
                e_context["reply"] = reply
            else:
                # 发送进度提醒消息
                ratio_display = self.extract_ratio_from_prompt(e_context["context"].content)
                progress_message = f"正在使用 {model} 模型以 {ratio_display} 比例生成图片，请稍候..."
                
                # 先发送进度提醒
                wait_reply = Reply(ReplyType.TEXT, progress_message)
                e_context["channel"].send(wait_reply, e_context["context"])
                
                # 生成图片
                image_url = self.generate_image(prompt_text, image_size, model, prompt_extend)
                logger.debug(f"[QwenImage] 生成的图片URL: {image_url}")

                if image_url:
                    # 发送图片
                    e_context["channel"].send(Reply(ReplyType.IMAGE_URL, image_url), e_context["context"])
                    logger.info(f"[QwenImage] 图片生成成功，URL: {image_url}")
                    # 不设置reply，因为已经通过channel发送了回复
                else:
                    logger.error("[QwenImage] 生成图片失败")
                    reply = Reply(ReplyType.ERROR, "生成图片失败。")
                    e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
        except Exception as e:
            logger.error(f"[QwenImage] 发生错误: {e}")
            reply = Reply(ReplyType.ERROR, f"发生错误: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def handle_control_command(self, e_context: EventContext):
        """处理智能扩写控制命令"""
        content = e_context["context"].content
        session_id = self.get_session_id(e_context["context"])
        logger.debug(f"[QwenImage] 收到控制消息: {content}")

        try:
            # 检查是开启还是禁用智能扩写
            if content.startswith("Q开启智能扩写"):
                self.user_prompt_extend_settings[session_id] = True
                reply = Reply(ReplyType.TEXT, "✅ 已开启智能扩写功能")
                logger.info(f"[QwenImage] 用户 {session_id} 开启智能扩写")
            elif content.startswith("Q禁用智能扩写"):
                self.user_prompt_extend_settings[session_id] = False
                reply = Reply(ReplyType.TEXT, "❌ 已禁用智能扩写功能")
                logger.info(f"[QwenImage] 用户 {session_id} 禁用智能扩写")
            else:
                reply = Reply(ReplyType.TEXT, "❓ 未知的控制命令")
            
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
        except Exception as e:
            logger.error(f"[QwenImage] 控制命令处理错误: {e}")
            reply = Reply(ReplyType.ERROR, f"控制命令处理错误: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def handle_account_command(self, e_context: EventContext):
        """处理账号切换命令"""
        content = e_context["context"].content
        logger.debug(f"[QwenImage] 收到账号切换消息: {content}")

        try:
            # 检查切换到哪个账号
            if content.startswith("Q切换账号 1"):
                if self.api_key_1:
                    self.current_api_key = self.api_key_1
                    self.current_account = 1
                    reply = Reply(ReplyType.TEXT, "✅ 已切换到账号 1")
                    logger.info(f"[QwenImage] 切换到账号 1")
                else:
                    reply = Reply(ReplyType.TEXT, "❌ 账号 1 未配置API密钥")
            elif content.startswith("Q切换账号 2"):
                if self.api_key_2:
                    self.current_api_key = self.api_key_2
                    self.current_account = 2
                    reply = Reply(ReplyType.TEXT, "✅ 已切换到账号 2")
                    logger.info(f"[QwenImage] 切换到账号 2")
                else:
                    reply = Reply(ReplyType.TEXT, "❌ 账号 2 未配置API密钥")
            else:
                reply = Reply(ReplyType.TEXT, "❓ 未知的账号切换命令")
            
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
        except Exception as e:
            logger.error(f"[QwenImage] 账号切换命令处理错误: {e}")
            reply = Reply(ReplyType.ERROR, f"账号切换命令处理错误: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def get_session_id(self, context):
        """获取会话ID，兼容不同的Context对象结构"""
        try:
            # 尝试从字典方式获取session_id
            if hasattr(context, '__getitem__'):
                return context.get("session_id", "default_user")
            # 尝试从属性方式获取session_id
            elif hasattr(context, 'session_id'):
                return context.session_id
            # 尝试从from_user_id获取
            elif hasattr(context, '__getitem__') and context.get("from_user_id"):
                return context.get("from_user_id")
            else:
                return "default_user"
        except Exception as e:
            logger.warning(f"[QwenImage] 获取session_id失败: {e}，使用默认值")
            return "default_user"

    def parse_user_input(self, content: str, context) -> Tuple[str, str, str, bool]:
        """解析用户输入，提取提示词、图片尺寸、模型和智能改写设置"""
        # 提取图片尺寸参数
        image_size = self.extract_image_size(content)
        
        # 提取模型参数
        model = self.extract_model(content)
        
        # 获取用户的智能改写设置
        session_id = self.get_session_id(context)
        prompt_extend = self.get_user_prompt_extend_setting(session_id)
        
        # 清理提示词，移除所有参数
        clean_prompt = self.clean_prompt_string(content)
        
        logger.debug(f"[QwenImage] 解析用户输入: 尺寸={image_size}, 模型={model}, 智能改写={prompt_extend}, 清理后的提示词={clean_prompt}")
        return clean_prompt, image_size, model, prompt_extend

    def get_user_prompt_extend_setting(self, session_id: str) -> bool:
        """获取用户的智能改写设置"""
        if session_id in self.user_prompt_extend_settings:
            return self.user_prompt_extend_settings[session_id]
        else:
            return self.global_prompt_extend  # 返回全局默认设置

    def extract_image_size(self, prompt: str) -> str:
        """提取图片尺寸参数"""
        match = re.search(r'--ar (\d+:\d+)', prompt)
        if match:
            ratio = match.group(1).strip()
            if ratio in self.ratios:
                width = self.ratios[ratio]["width"]
                height = self.ratios[ratio]["height"]
                size = f"{width}x{height}"
            else:
                size = f"{self.ratios[self.default_ratio]['width']}x{self.ratios[self.default_ratio]['height']}"
        else:
            # 使用默认尺寸
            size = f"{self.ratios[self.default_ratio]['width']}x{self.ratios[self.default_ratio]['height']}"
        
        logger.debug(f"[QwenImage] 提取的图片尺寸: {size}")
        return size

    def extract_model(self, prompt: str) -> str:
        """提取模型参数"""
        # 检查是否指定了plus模型
        if "--plus" in prompt:
            # 查找plus模型
            for model in self.models:
                if "plus" in model.lower():
                    logger.debug(f"[QwenImage] 检测到--plus参数，使用模型: {model}")
                    return model
        
        # 默认使用第一个模型（通常是flash模型）
        logger.debug(f"[QwenImage] 使用默认模型: {self.default_model}")
        return self.default_model

    def clean_prompt_string(self, prompt: str) -> str:
        """清理提示词，移除所有参数"""
        # 移除尺寸参数
        clean_prompt = re.sub(r'--ar \d+:\d+', '', prompt)
        # 移除模型参数
        clean_prompt = clean_prompt.replace('--plus', '')
        # 清理多余空格
        clean_prompt = re.sub(r'\s+', ' ', clean_prompt).strip()
        
        logger.debug(f"[QwenImage] 清理后的提示词: {clean_prompt}")
        return clean_prompt

    def extract_ratio_from_prompt(self, prompt: str) -> str:
        """从用户提示词中直接提取比例信息"""
        match = re.search(r'--ar (\d+:\d+)', prompt)
        if match:
            return match.group(1)
        else:
            return self.default_ratio  # 返回默认比例

    def generate_image(self, prompt: str, image_size: str, model: str, prompt_extend: bool) -> str:
        """调用Qwen Image API生成图片"""
        logger.info(f"[QwenImage] 准备调用Qwen Image API生成图片，模型: {model}, 尺寸: {image_size}, 智能改写: {prompt_extend}, 当前账号: {self.current_account}")

        # 构建请求体
        payload = {
            "model": model,
            "input": {
                "prompt": prompt
            },
            "parameters": {
                "size": image_size.replace('x', '*'),  # 将1024x1024转换为1024*1024
                "n": 1,
                "watermark": False,
                "prompt_extend": prompt_extend
            }
        }

        headers = {
            "X-DashScope-Async": "enable",
            "Authorization": f"Bearer {self.current_api_key}",
            "Content-Type": "application/json"
        }

        logger.debug(f"[QwenImage] 发送请求体: {payload}")
        logger.info(f"[QwenImage] 使用API URL: {self.base_url}")

        try:
            # 提交任务
            response = requests.post(self.base_url, headers=headers, json=payload, timeout=180)
            response.raise_for_status()
            task_data = response.json()
            
            # 获取任务ID
            task_id = task_data.get("output", {}).get("task_id")
            if not task_id:
                logger.error("❌ 未获取到任务ID")
                raise Exception("API响应中未获取到任务ID")
            
            logger.info(f"✅ 任务提交成功，任务ID: {task_id}")
            
            # 轮询任务结果
            return self._poll_task_result(task_id)
            
        except requests.exceptions.RequestException as e:
            logger.error(f"[QwenImage] API请求失败: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"[QwenImage] API响应状态码: {e.response.status_code}")
                logger.error(f"[QwenImage] API响应内容: {e.response.text}")
            raise Exception(f"API请求失败: {str(e)}")

    def _poll_task_result(self, task_id: str, max_retries: int = 60, retry_interval: int = 2) -> str:
        """轮询任务结果，获取生成的图像URL"""
        poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        headers = {
            "Authorization": f"Bearer {self.current_api_key}"
        }
        
        for attempt in range(max_retries):
            try:
                response = requests.get(poll_url, headers=headers, timeout=30)
                response.raise_for_status()
                result_data = response.json()
                
                task_status = result_data.get("output", {}).get("task_status")
                
                if task_status == "SUCCEEDED":
                    # 任务成功，获取结果图像
                    results = result_data.get("output", {}).get("results", [])
                    if results and len(results) > 0:
                        image_url = results[0].get("url")
                        if image_url:
                            logger.info("✅ 任务成功，获取到图像URL")
                            logger.info(f"🖼️ 图像URL: {image_url}")
                            return image_url
                        else:
                            logger.error("❌ 图像URL为空")
                            raise Exception("图像URL为空")
                    else:
                        logger.error("❌ 没有获取到结果")
                        raise Exception("没有获取到结果")
                
                elif task_status == "FAILED":
                    logger.error("❌ 任务执行失败")
                    error_code = result_data.get("output", {}).get("error_code", "未知")
                    error_message = result_data.get("output", {}).get("error_message", "未知")
                    raise Exception(f"任务执行失败: {error_code} - {error_message}")
                
                elif task_status in ["PENDING", "RUNNING"]:
                    # 任务还在进行中，等待后重试
                    if attempt % 10 == 0:  # 每10次重试打印一次状态
                        logger.info(f"⏳ 任务进行中... (第{attempt+1}次检查)")
                    time.sleep(retry_interval)
                    continue
                
                else:
                    logger.warning(f"⚠️ 未知任务状态: {task_status}")
                    time.sleep(retry_interval)
                    continue
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ 轮询请求失败: {e}")
                time.sleep(retry_interval)
                continue
            except Exception as e:
                logger.error(f"❌ 轮询处理失败: {e}")
                time.sleep(retry_interval)
                continue
        
        logger.error("❌ 轮询超时")
        raise Exception("轮询超时，请稍后手动查询任务状态")

    def get_help_text(self, **kwargs):
        """获取帮助文本"""
        help_text = "Qwen Image 插件使用指南：\n"
        help_text += f"1. 使用 {', '.join(self.drawing_prefixes)} 作为画图命令前缀\n"
        help_text += "2. 使用 '--ar' 后跟比例来指定图片尺寸，例如：--ar 16:9\n"
        help_text += "3. 使用 '--plus' 参数调用plus模型（默认使用flash模型）\n"
        help_text += f"4. 使用 {', '.join(self.control_prefixes)} 控制智能扩写功能\n"
        help_text += f"5. 使用 {', '.join(self.account_prefixes)} 切换API账号\n"
        help_text += f"示例：{self.drawing_prefixes[0]} 一只可爱的小猫 --ar 16:9\n"
        help_text += f"示例：{self.drawing_prefixes[0]} 一张酷炫的电影海报 --ar 3:4 --plus\n"
        help_text += f"可用的尺寸比例：{', '.join(self.ratios.keys())}\n"
        help_text += f"默认尺寸比例：{self.default_ratio}\n"
        help_text += f"可用模型：{', '.join(self.models)}\n"
        help_text += f"当前账号：{self.current_account}\n"
        help_text += "注意：智能改写功能对短提示词效果提升明显\n"
        return help_text 