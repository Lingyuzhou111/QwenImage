import os
import re
import json
import requests
import time
import base64
from typing import Tuple
from PIL import Image
from io import BytesIO

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
            self.models = qwen_config.get("model", ["qwen-image", "wan2.2-t2i-flash", "wan2.2-t2i-plus"])
            self.default_model = "qwen-image"  # 默认使用qwen-image模型
            
            # 读取Qwen Image Edit配置  
            qwen_edit_config = conf.get("qwen_image_edit", {})
            self.edit_base_url = qwen_edit_config.get("base_url", "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")
            self.edit_models = qwen_edit_config.get("model", ["qwen-image-edit"])
            self.default_edit_model = "qwen-image-edit"  # 默认使用qwen-image-edit模型
            
            # API密钥配置（文生图和图生图共用）
            self.api_key_1 = conf.get("api_key_1", "")
            self.api_key_2 = conf.get("api_key_2", "")
            self.current_api_key = self.api_key_1  # 默认使用第一个API密钥
            self.current_account = 1  # 当前使用的账号编号
            
            # 绘图命令前缀
            self.drawing_prefixes = conf.get("image_command", ["Q画图", "Q生成"])
            
            # 图像编辑命令前缀
            self.edit_prefixes = conf.get("image_edit_command", ["Q改图", "Q编辑"])
            
            # 控制命令前缀
            self.control_prefixes = conf.get("control_command", ["Q开启智能扩写", "Q禁用智能扩写"])
            
            # 账号切换命令前缀
            self.account_prefixes = conf.get("account_command", ["Q切换账号 1", "Q切换账号 2"])
            
            # 图片比例配置
            self.ratios = qwen_config.get("ratios", {
                "1:1": {"width": 1328, "height": 1328},
                "3:4": {"width": 1140, "height": 1472},
                "4:3": {"width": 1472, "height": 1140},
                "16:9": {"width": 1664, "height": 928},
                "9:16": {"width": 928, "height": 1664}
            })
            self.default_ratio = qwen_config.get("default_ratio", "1:1")
            
            # 默认负面提示词配置
            self.default_negative_prompt = qwen_config.get("default_negative_prompt", "色调艳丽，过曝，静态，细节模糊不清，风格，画面，整体发灰，最差质量，低质量， JPEG压缩残留，丑陋的，残缺的，多余的手指，杂乱的背景，三条腿")
            
            # 用户状态管理（用于存储每个用户的智能扩写设置）
            self.user_prompt_extend_settings = {}  # 用户ID -> 智能扩写设置
            self.global_prompt_extend = True  # 全局默认智能扩写设置
            
            # 图像编辑状态管理（用于存储等待上传图片的用户）
            self.pending_edit_users = {}  # 用户ID -> 编辑指令

            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

            logger.info(f"[QwenImage] 初始化成功，文生图模型: {self.models}，图生图模型: {self.edit_models}")
        except Exception as e:
            logger.error(f"[QwenImage] 初始化失败，错误：{e}")
            raise e

    def on_handle_context(self, e_context: EventContext):
        context_type = e_context["context"].type
        session_id = self.get_session_id(e_context["context"])
        content = e_context["context"].content
        
        # 获取消息对象用于检查引用图片
        msg_from_context = e_context["context"]
        actual_msg_object = msg_from_context.kwargs.get('msg') if hasattr(msg_from_context, 'kwargs') else None

        # 检查是否是引用图片的Q改图命令（最高优先级）
        if actual_msg_object and \
           hasattr(actual_msg_object, 'is_processed_image_quote') and \
           actual_msg_object.is_processed_image_quote and \
           hasattr(actual_msg_object, 'referenced_image_path') and \
           actual_msg_object.referenced_image_path and \
           context_type == ContextType.TEXT and \
           content and content.startswith(tuple(self.edit_prefixes)):
            
            logger.info(f"[QwenImage] 检测到引用图片的Q改图命令: {content}")
            self.handle_referenced_image_edit(e_context, content, actual_msg_object.referenced_image_path)
            return
        
        # 处理图像输入（用于图像编辑）
        if context_type == ContextType.IMAGE:
            if session_id in self.pending_edit_users:
                self.handle_image_upload(e_context)
            return
        
        # 处理文本输入
        if context_type != ContextType.TEXT:
            return
        
        # 检查是否是绘图命令
        if content.startswith(tuple(self.drawing_prefixes)):
            self.handle_drawing_command(e_context)
        # 检查是否是图像编辑命令
        elif content.startswith(tuple(self.edit_prefixes)):
            self.handle_edit_command(e_context)
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
            prompt_text, image_size, model, prompt_extend, negative_prompt = self.parse_user_input(content, e_context["context"])
            logger.debug(f"[QwenImage] 解析后的参数: 提示词={prompt_text}, 尺寸={image_size}, 模型={model}")

            if not prompt_text:
                reply = Reply(ReplyType.TEXT, "请输入需要生成的图片描述")
                e_context["reply"] = reply
            else:
                # 发送进度提醒消息
                ratio_display = self.extract_ratio_from_prompt(e_context["context"].content)
                progress_message = f"🌁正在使用 {model} 模型以 {ratio_display} 比例生成图片，请稍候..."
                
                # 先发送进度提醒
                wait_reply = Reply(ReplyType.TEXT, progress_message)
                e_context["channel"].send(wait_reply, e_context["context"])
                
                # 生成图片
                image_url = self.generate_image(prompt_text, image_size, model, prompt_extend, negative_prompt)
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

    def handle_edit_command(self, e_context: EventContext):
        """处理图像编辑命令"""
        content = e_context["context"].content
        session_id = self.get_session_id(e_context["context"])
        logger.debug(f"[QwenImage] 收到图像编辑消息: {content}")

        try:
            # 移除前缀，获取编辑指令
            edit_prompt = None
            for prefix in self.edit_prefixes:
                if content.startswith(prefix):
                    edit_prompt = content[len(prefix):].strip()
                    break

            if not edit_prompt:
                reply = Reply(ReplyType.TEXT, "请输入需要对图像进行的编辑指令")
                e_context["reply"] = reply
            else:
                # 将用户加入等待上传图片的队列
                self.pending_edit_users[session_id] = {
                    "prompt": edit_prompt,
                    "timestamp": time.time()
                }
                
                # 提示用户上传图片
                reply = Reply(ReplyType.TEXT, "请在3分钟内上传需要编辑的图片")
                e_context["reply"] = reply
                logger.info(f"[QwenImage] 用户 {session_id} 发起图像编辑请求: {edit_prompt}")
                
                # 设置3分钟后清理超时请求的定时器
                import threading
                def cleanup_timeout_request():
                    time.sleep(180)  # 3分钟
                    if session_id in self.pending_edit_users:
                        timestamp = self.pending_edit_users[session_id].get("timestamp", 0)
                        if time.time() - timestamp >= 180:  # 超过3分钟
                            del self.pending_edit_users[session_id]
                            logger.info(f"[QwenImage] 清理用户 {session_id} 的超时图像编辑请求")
                
                threading.Thread(target=cleanup_timeout_request, daemon=True).start()
                
            e_context.action = EventAction.BREAK_PASS
        except Exception as e:
            logger.error(f"[QwenImage] 图像编辑命令处理错误: {e}")
            reply = Reply(ReplyType.ERROR, f"图像编辑命令处理错误: {str(e)}")
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

    def handle_referenced_image_edit(self, e_context: EventContext, content: str, referenced_image_path: str):
        """处理引用图片的Q改图命令
        Args:
            e_context: 事件上下文
            content: 命令内容
            referenced_image_path: 引用图片的路径
        """
        context = e_context['context']
        session_id = self.get_session_id(context)
        
        logger.info(f"[QwenImage] 处理引用图片改图命令: {content}, 图片路径: {referenced_image_path}")
        
        try:
            # 移除前缀，获取编辑指令
            edit_prompt = None
            for prefix in self.edit_prefixes:
                if content.startswith(prefix):
                    edit_prompt = content[len(prefix):].strip()
                    break

            if not edit_prompt:
                e_context['reply'] = Reply(ReplyType.TEXT, "请输入需要对图像进行的编辑指令")
                e_context.action = EventAction.BREAK_PASS
                return

            # 发送进度消息
            progress_message = f"🌁正在使用 {self.default_edit_model} 模型处理引用的图片，请稍候..."
            progress_reply = Reply(ReplyType.TEXT, progress_message)
            e_context["channel"].send(progress_reply, context)
            
            logger.info(f"[QwenImage] 开始为用户 {session_id} 编辑引用图片，指令: {edit_prompt}")
            
            # 获取引用图片数据
            image_data = self._get_referenced_image_data(referenced_image_path)
            if not image_data:
                error_reply = Reply(ReplyType.TEXT, "引用图片获取失败，请重新尝试")
                e_context["channel"].send(error_reply, context)
                e_context.action = EventAction.BREAK_PASS
                return
            
            # 调用图像编辑API，传入图片数据而不是路径
            edited_image_url = self.edit_image(image_data, edit_prompt)
            
            if edited_image_url:
                # 发送编辑后的图片
                e_context["channel"].send(Reply(ReplyType.IMAGE_URL, edited_image_url), context)
                logger.info(f"[QwenImage] 引用图片编辑成功，URL: {edited_image_url}")
            else:
                logger.error("[QwenImage] 引用图片编辑失败")
                reply = Reply(ReplyType.ERROR, "引用图片编辑失败。")
                e_context["channel"].send(reply, context)
                
            e_context.action = EventAction.BREAK_PASS
        except Exception as e:
            logger.error(f"[QwenImage] 引用图片编辑处理错误: {e}")
            reply = Reply(ReplyType.ERROR, f"引用图片编辑处理错误: {str(e)}")
            e_context["channel"].send(reply, context)
            e_context.action = EventAction.BREAK_PASS

    def handle_image_upload(self, e_context: EventContext):
        """处理用户上传的图像，进行图像编辑"""
        session_id = self.get_session_id(e_context["context"])
        logger.debug(f"[QwenImage] 用户 {session_id} 上传了图片")

        try:
            if session_id not in self.pending_edit_users:
                logger.warning(f"[QwenImage] 用户 {session_id} 不在等待队列中")
                return

            # 获取编辑指令
            edit_info = self.pending_edit_users[session_id]
            edit_prompt = edit_info["prompt"]
            
            # 从等待队列中移除用户
            del self.pending_edit_users[session_id]
            
            # 发送进度提醒消息
            progress_message = f"🌁正在使用 {self.default_edit_model} 模型编辑图片，请稍候..."
            wait_reply = Reply(ReplyType.TEXT, progress_message)
            e_context["channel"].send(wait_reply, e_context["context"])
            
            logger.info(f"[QwenImage] 开始为用户 {session_id} 编辑图片，指令: {edit_prompt}")
            
            # 调用图像编辑API
            edited_image_url = self.edit_image(e_context["context"].content, edit_prompt)
            
            if edited_image_url:
                # 发送编辑后的图片
                e_context["channel"].send(Reply(ReplyType.IMAGE_URL, edited_image_url), e_context["context"])
                logger.info(f"[QwenImage] 图片编辑成功，URL: {edited_image_url}")
            else:
                logger.error("[QwenImage] 图片编辑失败")
                reply = Reply(ReplyType.ERROR, "图片编辑失败。")
                e_context["reply"] = reply
                
            e_context.action = EventAction.BREAK_PASS
        except Exception as e:
            logger.error(f"[QwenImage] 图像上传处理错误: {e}")
            reply = Reply(ReplyType.ERROR, f"图像上传处理错误: {str(e)}")
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

    def parse_user_input(self, content: str, context) -> Tuple[str, str, str, bool, str]:
        """解析用户输入，提取提示词、图片尺寸、模型、智能改写设置和负面提示词"""
        # 提取图片尺寸参数
        image_size = self.extract_image_size(content)
        
        # 提取模型参数
        model = self.extract_model(content)
        
        # 获取用户的智能改写设置
        session_id = self.get_session_id(context)
        prompt_extend = self.get_user_prompt_extend_setting(session_id)
        
        # 提取负面提示词
        negative_prompt = self.extract_negative_prompt(content)
        
        # 清理提示词，移除所有参数
        clean_prompt = self.clean_prompt_string(content)
        
        logger.debug(f"[QwenImage] 解析用户输入: 尺寸={image_size}, 模型={model}, 智能改写={prompt_extend}, 负面提示词={negative_prompt}, 清理后的提示词={clean_prompt}")
        return clean_prompt, image_size, model, prompt_extend, negative_prompt

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
        # 检查是否指定了flash模型
        if "--flash" in prompt:
            # 查找flash模型
            for model in self.models:
                if "flash" in model.lower():
                    logger.debug(f"[QwenImage] 检测到--flash参数，使用模型: {model}")
                    return model
        
        # 检查是否指定了plus模型
        elif "--plus" in prompt:
            # 查找plus模型
            for model in self.models:
                if "plus" in model.lower():
                    logger.debug(f"[QwenImage] 检测到--plus参数，使用模型: {model}")
                    return model
        
        # 默认使用qwen-image模型
        logger.debug(f"[QwenImage] 使用默认模型: {self.default_model}")
        return self.default_model

    def clean_prompt_string(self, prompt: str) -> str:
        """清理提示词，移除所有参数"""
        # 移除尺寸参数
        clean_prompt = re.sub(r'--ar \d+:\d+', '', prompt)
        # 移除模型参数
        clean_prompt = clean_prompt.replace('--plus', '')
        clean_prompt = clean_prompt.replace('--flash', '')
        # 移除负面提示词参数
        clean_prompt = re.sub(r'--负面提示：[^，。！？]*', '', clean_prompt)
        # 清理多余空格
        clean_prompt = re.sub(r'\s+', ' ', clean_prompt).strip()
        
        logger.debug(f"[QwenImage] 清理后的提示词: {clean_prompt}")
        return clean_prompt

    def extract_negative_prompt(self, prompt: str) -> str:
        """从用户提示词中提取负面提示词"""
        # 尝试从提示词中提取负面提示词
        match = re.search(r'--负面提示：(.+?)(?=\s*--|\s*$)', prompt)
        if match:
            negative_prompt = match.group(1).strip()
            logger.debug(f"[QwenImage] 从提示词中提取负面提示词: {negative_prompt}")
            return negative_prompt
        else:
            # 如果没有负面提示词，则使用默认的负面提示词
            logger.debug(f"[QwenImage] 使用默认负面提示词: {self.default_negative_prompt}")
            return self.default_negative_prompt

    def extract_ratio_from_prompt(self, prompt: str) -> str:
        """从用户提示词中直接提取比例信息"""
        match = re.search(r'--ar (\d+:\d+)', prompt)
        if match:
            return match.group(1)
        else:
            return self.default_ratio  # 返回默认比例

    def generate_image(self, prompt: str, image_size: str, model: str, prompt_extend: bool, negative_prompt: str) -> str:
        """调用Qwen Image API生成图片"""
        logger.info(f"[QwenImage] 准备调用Qwen Image API生成图片，模型: {model}, 尺寸: {image_size}, 智能改写: {prompt_extend}, 负面提示词: {negative_prompt}, 当前账号: {self.current_account}")

        # 构建请求体
        payload = {
            "model": model,
            "input": {
                "prompt": prompt,
                "negative_prompt": negative_prompt
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

    def edit_image(self, image_content, edit_prompt):
        """调用Qwen Image Edit API编辑图片
        Args:
            image_content: 可以是文件路径(str)或图片二进制数据(bytes)
            edit_prompt: 编辑指令
        """
        logger.info(f"[QwenImage] 准备调用Qwen Image Edit API编辑图片，模型: {self.default_edit_model}")
        logger.info(f"[QwenImage] 编辑指令: {edit_prompt}")

        if not self.current_api_key:
            logger.error("[QwenImage] 未配置Qwen API Key")
            raise Exception("未配置API Key")

        try:
            # 将图片内容转换为base64格式
            image_base64 = self._process_image_to_base64(image_content)
            logger.info(f"[QwenImage] 📷 图片已转换为base64格式")
        except Exception as e:
            logger.error(f"[QwenImage] ❌ 图片转base64失败: {e}")
            raise Exception(f"图片转base64失败 - {e}")

        # 构造API请求，参考ComfyUI节点的实现
        payload = {
            "model": self.default_edit_model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "image": image_base64
                            },
                            {
                                "text": edit_prompt
                            }
                        ]
                    }
                ]
            },
            "parameters": {
                "negative_prompt": self.default_negative_prompt,
                "prompt_extend": True,
                "watermark": False
            }
        }

        headers = {
            "Authorization": f"Bearer {self.current_api_key}",
            "Content-Type": "application/json"
        }

        logger.debug(f"[QwenImage] 发送请求体: {payload}")
        logger.info(f"[QwenImage] 使用API URL: {self.edit_base_url}")

        try:
            # 发送请求
            logger.info("[QwenImage] 🚀 发送API请求...")
            response = requests.post(self.edit_base_url, headers=headers, json=payload, timeout=180)
            response.raise_for_status()
            
            result_data = response.json()
            logger.info("[QwenImage] ✅ API请求成功")
            
            # 解析响应，参考ComfyUI节点的实现
            choices = result_data.get("output", {}).get("choices", [])
            if choices and len(choices) > 0:
                content = choices[0].get("message", {}).get("content", [])
                
                # 查找图像内容
                image_content = None
                for item in content:
                    if "image" in item:
                        image_content = item["image"]
                        break
                
                if image_content:
                    logger.info("[QwenImage] 🖼️ 获取到编辑结果")
                    logger.info(f"🖼️ 图像URL: {image_content}")
                    return image_content
                else:
                    logger.error("[QwenImage] ❌ 响应中未找到图像内容")
                    raise Exception("响应中未找到图像内容")
            else:
                logger.error("[QwenImage] ❌ 响应格式异常")
                raise Exception("响应格式异常")
                
        except requests.exceptions.RequestException as e:
            logger.error(f"[QwenImage] ❌ API请求失败: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"[QwenImage] API响应状态码: {e.response.status_code}")
                logger.error(f"[QwenImage] API响应内容: {e.response.text}")
            raise Exception(f"API请求失败: {str(e)}")

    def _process_image_to_base64(self, image_content):
        """将图像内容转换为base64格式"""
        try:
            # 如果image_content是文件路径，直接读取文件
            if isinstance(image_content, str) and os.path.exists(image_content):
                with open(image_content, 'rb') as image_file:
                    image_data = image_file.read()
            # 如果image_content是URL，下载图片
            elif isinstance(image_content, str) and (image_content.startswith('http://') or image_content.startswith('https://')):
                response = requests.get(image_content, timeout=60)
                response.raise_for_status()
                image_data = response.content
            # 如果image_content是bytes数据
            elif isinstance(image_content, bytes):
                image_data = image_content
            else:
                # 尝试处理其他格式
                logger.warning(f"[QwenImage] 未知的图像内容格式: {type(image_content)}")
                # 如果是字符串，假设是base64编码的图片数据
                if isinstance(image_content, str):
                    try:
                        image_data = base64.b64decode(image_content)
                    except:
                        raise Exception(f"无法处理的图像内容格式: {type(image_content)}")
                else:
                    raise Exception(f"无法处理的图像内容格式: {type(image_content)}")
            
            # 验证图片格式并转换为JPEG
            img = Image.open(BytesIO(image_data))
            
            # 确保是RGB格式
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # 保存为JPEG格式并转换为base64
            output_buffer = BytesIO()
            img.save(output_buffer, format="JPEG", quality=95)
            image_data_bytes_jpeg = output_buffer.getvalue()
            image_base64 = base64.b64encode(image_data_bytes_jpeg).decode('utf-8')
            
            return f"data:image/jpeg;base64,{image_base64}"
            
        except Exception as e:
            logger.error(f"[QwenImage] 图像转换失败: {e}")
            raise

    def _get_referenced_image_data(self, referenced_image_path: str):
        """获取引用图片的数据
        Args:
            referenced_image_path: 引用图片的路径
        Returns:
            bytes: 图片二进制数据，失败返回None
        """
        try:
            logger.info(f"[QwenImage] 获取引用图片数据: {referenced_image_path}")
            
            # 如果是文件路径，直接读取
            if os.path.isfile(referenced_image_path):
                logger.info(f"[QwenImage] 从文件路径读取引用图片: {referenced_image_path}")
                with open(referenced_image_path, 'rb') as f:
                    return f.read()
            
            # 如果是网络URL，下载图片
            if referenced_image_path.startswith('http://') or referenced_image_path.startswith('https://'):
                logger.info(f"[QwenImage] 下载引用图片: {referenced_image_path}")
                response = requests.get(referenced_image_path, timeout=30)
                if response.status_code == 200:
                    return response.content
            
            # 检查临时目录中的文件
            if referenced_image_path.startswith('tmp/') and not os.path.isabs(referenced_image_path):
                temp_dir = "tmp"  # 使用固定的tmp目录
                potential_path = os.path.join(temp_dir, os.path.basename(referenced_image_path))
                if os.path.isfile(potential_path):
                    logger.info(f"[QwenImage] 从临时目录读取引用图片: {potential_path}")
                    with open(potential_path, 'rb') as f:
                        return f.read()
            
            # 检查微信图片缓存目录
            if 'wx859_img_cache' in referenced_image_path:
                logger.info(f"[QwenImage] 尝试从微信缓存目录读取: {referenced_image_path}")
                if os.path.isfile(referenced_image_path):
                    with open(referenced_image_path, 'rb') as f:
                        return f.read()
            
            logger.error(f"[QwenImage] 无法找到引用图片: {referenced_image_path}")
            return None
            
        except Exception as e:
            logger.error(f"[QwenImage] 获取引用图片数据时发生错误: {str(e)}")
            return None

    def get_help_text(self, **kwargs):
        """获取帮助文本"""
        help_text = "Qwen Image 插件使用指南：\n\n"
        
        # 文生图功能
        help_text += "【文生图功能】\n"
        help_text += f"1. 使用 {', '.join(self.drawing_prefixes)} 作为画图命令前缀\n"
        help_text += "2. 使用 '--ar' 后跟比例来指定图片尺寸，例如：--ar 16:9\n"
        help_text += "3. 使用 '--flash' 参数调用flash模型，使用 '--plus' 参数调用plus模型（默认使用qwen-image模型）\n"
        help_text += "4. 使用 '--负面提示：内容' 指定负面提示词\n"
        help_text += f"示例：{self.drawing_prefixes[0]} 一只可爱的小猫 --ar 16:9\n"
        help_text += f"示例：{self.drawing_prefixes[0]} 一张酷炫的电影海报 --ar 3:4 --plus\n"
        help_text += f"示例：{self.drawing_prefixes[0]} 快速生成的风景画 --ar 16:9 --flash\n"
        help_text += f"示例：{self.drawing_prefixes[0]} 美丽的花朵 --负面提示：模糊，低质量\n\n"
        
        # 图生图功能
        help_text += "【图生图功能】\n"
        help_text += f"1. 使用 {', '.join(self.edit_prefixes)} 作为图像编辑命令前缀\n"
        help_text += "2. 支持两种操作模式：\n"
        help_text += "   ◆ 等待模式：先发编辑指令，再上传图片（3分钟有效）\n"
        help_text += "   ◆ 引用模式：先发图片，再引用图片消息发编辑指令\n"
        help_text += "3. 插件会自动使用qwen-image-edit模型进行图像编辑\n"
        help_text += f"等待模式示例：{self.edit_prefixes[0]} 保持人物一致性，将图片变成复古日漫风格\n"
        help_text += f"引用模式示例：[发送图片] → [引用该图片] {self.edit_prefixes[0]} 将背景改成海滩场景\n"
        help_text += f"引用模式示例：[发送图片] → [引用该图片] {self.edit_prefixes[0]} 添加眼镜和帽子\n\n"
        
        # 控制功能
        help_text += "【控制功能】\n"
        help_text += f"1. 使用 {', '.join(self.control_prefixes)} 控制智能扩写开关\n"
        help_text += f"2. 使用 {', '.join(self.account_prefixes)} 切换API账号\n\n"
        
        help_text += "注意：智能改写功能对短提示词效果提升明显\n"
        help_text += "注意：如果不指定负面提示词，将使用默认的负面提示词\n"
        help_text += "注意：图像编辑功能需要在3分钟内上传图片，超时后需要重新发起请求\n"
        return help_text 
