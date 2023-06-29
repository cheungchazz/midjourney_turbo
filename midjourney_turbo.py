#!/usr/bin/env python
# -*- coding=utf-8 -*-
"""
@time: 2023/5/25 10:46
@Project ：chatgpt-on-wechat
@file: midjourney_turbo.py
"""
import base64
import json
import re
import time
import openai
import requests
import io
import os

from PIL import Image
from plugins.midjourney_turbo.lib.midJourney_module import MidJourneyModule
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from channel.wechatcom.wechatcomapp_channel import WechatComAppChannel
from channel.wechat.wechat_channel import WechatChannel
from channel.wechatmp.wechatmp_channel import WechatMPChannel
from config import conf
import plugins
from plugins import *
from common.log import logger
from common.expired_dict import ExpiredDict
from datetime import timedelta


# 定义一个函数 is_chinese, 该函数接收一个字符串参数 prompt
def create_channel_object():
    channel_type = conf().get("channel_type")
    if channel_type in ['wechat', 'wx', 'wxy']:
        return WechatChannel()
    elif channel_type == 'wechatmp':
        return WechatMPChannel()
    elif channel_type == 'wechatmp_service':
        return WechatMPChannel()
    elif channel_type == 'wechatcom_app':
        return WechatComAppChannel()
    else:
        return WechatChannel()


def format_content(content):
    if "—" in content:
        content = content.replace("—", "--")
    if "--" in content:
        prompt, commands = content.split("--", 1)
        commands = " --" + commands.strip()
    else:
        prompt, commands = content, ""

    return prompt, commands


def generate_prompt(content):
    message_content = "请根据AI生图关键词'{}'预测想要得到的画面，然后用英文拓展描述、丰富细节、添加关键词描述以适用于AI生图。描述要简短直接突出重点，请把优化后的描述直接返回，不需要多余的语言！".format(
        content)
    completion = openai.ChatCompletion.create(model=conf().get("model"), messages=[
        {"role": "user", "content": message_content}], max_tokens=300, temperature=0.8, top_p=0.9)
    prompt = completion['choices'][0]['message']['content']
    logger.debug("优化后的关键词：{}".format(prompt))
    return prompt


def convert_base64(image):
    with open(image, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read())
    return encoded_string.decode('utf-8')


def download_and_compress_image(url, filename, quality=30):
    # 确定保存图片的目录
    directory = os.path.join(os.getcwd(), "tmp")
    # 如果目录不存在，则创建目录
    if not os.path.exists(directory):
        os.makedirs(directory)

    # 下载图片
    response = requests.get(url)
    image = Image.open(io.BytesIO(response.content))

    # 压缩图片
    image_path = os.path.join(directory, f"{filename}.jpg")
    image.save(image_path, "JPEG", quality=quality)

    return image_path


def send_with_retry(comapp, com_reply, e_context, max_retries=3, delay=2):
    for i in range(max_retries):
        try:
            comapp.send(com_reply, e_context['context'])
            break  # 如果成功发送，就跳出循环
        except requests.exceptions.SSLError as e:
            logger.error(f"Failed to send message due to SSL error: {e}. Attempt {i + 1} of {max_retries}")
            if i < max_retries - 1:  # 如果不是最后一次尝试，那么等待一段时间再重试
                time.sleep(delay)  # 等待指定的秒数
            else:
                logger.error(f"Failed to send message after {max_retries} attempts. Giving up.")


@plugins.register(name="Midjourney_Turbo", desc="使用Midjourney来画图", desire_priority=1, version="0.1",
                  author="chazzjimel")
class MidjourneyTurbo(Plugin):  # 定义一个 MidjourneyV2 类，继承自 Plugin
    def __init__(self):  # 类的初始化函数
        super().__init__()  # 调用父类的初始化函数
        try:
            curdir = os.path.dirname(__file__)  # 获取当前脚本的文件路径
            config_path = os.path.join(curdir, "config.json")  # 定义配置文件的路径
            self.params_cache = ExpiredDict(60 * 60)  # 创建一个过期字典，键值对在一小时后过期
            if not os.path.exists(config_path):  # 如果配置文件不存在
                logger.info('[RP] 配置文件不存在，将使用config.json.template模板')  # 输出日志信息
                config_path = os.path.join(curdir, "config.json.template")  # 则使用模板配置文件的路径
            with open(config_path, "r", encoding="utf-8") as f:  # 以只读模式打开配置文件
                config = json.load(f)  # 加载 JSON 文件内容到 config 变量
                self.comapp = create_channel_object()
                self.api_key = config.get("api_key", "")
                self.domain_name = config["domain_name"]
                self.image_ins = config.get("image_ins", "/p")
                self.blend_ins = config.get("blend_ins", "/b")
                self.change_ins = config.get("change_ins", "/c")
                self.split_url = config.get("split_url", False)
                self.short_url_api = config.get("short_url_api", "")
                self.default_params = config.get("default_params", {"action": "IMAGINE:出图", "prompt": ""})
                self.gpt_optimized = config.get("gpt_optimized", False)
                self.complete_prompt = config.get("complete_prompt", "任务完成！")
                self.mm = MidJourneyModule(api_key=self.api_key, domain_name=self.domain_name)
                if not self.domain_name or "你的域名" in self.domain_name:
                    raise Exception("please set your Midjourney domain_name in config or environment variable.")
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context  # 设置事件处理函数
            logger.info("[RP] inited")  # 输出日志信息，表示插件已初始化
        except Exception as e:  # 捕获所有的异常
            if isinstance(e, FileNotFoundError):  # 如果是 FileNotFoundError 异常
                logger.warn(f"[RP] init failed, config.json not found.")  # 则输出日志信息，表示配置文件未找到
            else:  # 如果是其他类型的异常
                logger.warn("[RP] init failed." + str(e))  # 则输出日志信息，表示初始化失败，并附加异常信息
            raise e  # 抛出异常，结束程序

    # 定义了一个事件处理方法，当插件接收到指定类型的事件时，会调用这个方法处理事件
    def on_handle_context(self, e_context: EventContext):
        if e_context['context'].type not in [ContextType.IMAGE_CREATE, ContextType.IMAGE]:  # 如果事件的类型不是创建图片或者图片类型，则直接返回
            return
        logger.info("[RP] image_query={}".format(e_context['context'].content))
        reply = Reply()  # 创建一个回复对象
        try:  # 异常处理
            user_id = e_context['context']["session_id"]  # 获取会话ID
            content = e_context['context'].content[:]  # 获取内容
            if e_context['context'].type == ContextType.IMAGE_CREATE:  # 如果事件类型是创建图片
                self.handle_image_create(e_context, user_id, content, reply)
            elif user_id in self.params_cache:
                self.handle_params_cache(e_context, user_id, content, reply)
            e_context['reply'] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束后，跳过处理context的默认逻辑
            logger.debug("Event action set to BREAK_PASS, reply set.")
        except Exception as e:  # 处理异常情况
            reply.type = ReplyType.ERROR
            reply.content = "[RP] " + str(e)
            e_context['reply'] = reply
            logger.exception("[RP] exception: %s" % e)
            e_context.action = EventAction.CONTINUE

    def handle_image_create(self, e_context, user_id, content, reply):
        prompt, commands = format_content(content=content)
        params = {**self.default_params}
        if self.image_ins in prompt:  # 处理垫图，示例输入：/p prompt
            prompt = prompt.replace(self.image_ins, "")
            self.params_cache[user_id] = {'image_params': params}
            if params.get("prompt", ""):
                params["prompt"] += f", {prompt}"
            else:
                params["prompt"] += f"{prompt}"
            logger.info("[RP] params={}".format(params))  # 记录日志
            reply.type = ReplyType.INFO
            reply.content = "请发送一张图片给我"
        elif self.blend_ins in prompt:  # 处理合图，示例输入：/b
            logger.info("[RP] blend_ins prompt={}".format(prompt))
            try:
                num_pictures = int(prompt.split()[1])
            except (IndexError, ValueError):
                trigger = conf()['image_create_prefix'][0]
                reply.type = ReplyType.ERROR
                reply.content = f"指令不正确，请根据示例格式重新输入：{trigger} {self.blend_ins} 2\n合图数量仅限2-5张"
                e_context['reply'] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            if not 2 <= num_pictures <= 5:
                trigger = conf()['image_create_prefix'][0]
                reply.type = ReplyType.ERROR
                reply.content = f"指令不正确，请根据示例格式重新输入：{trigger} {self.blend_ins} 2\n合图数量仅限2-5张"
                e_context['reply'] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            # 创建一个空的base64_data列表
            self.params_cache[user_id] = {'blend_params': params, 'num_pictures': num_pictures,
                                          'base64_data': []}
            logger.debug(f"self.params_cache_2:{self.params_cache}")
            if params.get("prompt", ""):
                params["prompt"] += f", {prompt}"
            else:
                params["prompt"] += f"{prompt}"
            logger.info("[RP] params={}".format(params))  # 记录日志
            reply.type = ReplyType.INFO
            reply.content = f"请直接发送{num_pictures}张图片给我"
        elif self.change_ins in prompt:  # 处理变换，示例输入：/c V/U 1-4
            submit_uv = ' '.join(prompt.replace(self.change_ins, "").strip().split())
            logger.debug("[RP] submit_uv post_json={}".format(" ".join(submit_uv)))
            # 检查输入的格式是否正确
            pattern = re.compile(r'^\d+\s[VU]\d$')
            if not pattern.match(submit_uv):
                trigger = conf()['image_create_prefix'][0]
                reply.type = ReplyType.ERROR
                reply.content = f"格式不正确。请使用如下示例格式：\n{trigger} {self.change_ins} 8528881058085979 V1"
            else:
                # 解析输入的值
                number, v_value = submit_uv.split()
                logger.debug("Parsed values: Number: {}, V value: {}".format(number, v_value))
                if v_value in ["U1", "U2", "U3", "U4", "V1", "V2", "V3", "V4"]:
                    simple_data = self.mm.get_simple(content=number + " " + v_value)
                    self.send_task_submission_message(e_context, messageId=simple_data["result"])
                    task_data = self.mm.get_image_url(id=simple_data["result"])
                    if task_data["failReason"] is None:
                        if self.split_url:
                            split_url = task_data["imageUrl"].split('/')
                            new_url = '/'.join(split_url[0:3] + split_url[5:])
                        else:
                            new_url = task_data["imageUrl"]
                        short_url = self.get_short_url(short_url_api=self.short_url_api, url=new_url)
                        self.time_diff_start_finish_td, self.time_diff_submit_finish_td = self.get_time_diff(task_data)
                        logger.debug("new_url: %s" % new_url)
                        com_reply = Reply()
                        com_reply.type = ReplyType.IMAGE
                        image_path = download_and_compress_image(new_url, simple_data['result'])
                        image_storage = open(image_path, 'rb')
                        com_reply.content = image_storage
                        # com_reply.content = task_data["imageUrl"]  # 这里涉及到地址反代的操作，正常主域名反代或没有反代则使用此默认
                        send_with_retry(self.comapp, com_reply, e_context)
                        logger.debug("The comapp object is an instance of: " + type(self.comapp).__name__)
                        reply.type = ReplyType.TEXT
                        reply.content = self.complete_prompt.format(id=simple_data["result"],
                                                                    change_ins=self.change_ins, imgurl=short_url,
                                                                    start_finish=self.time_diff_start_finish_td,
                                                                    submit_finish=self.time_diff_submit_finish_td)
                        logger.debug("Sent image URL and completed prompt.")
                    else:
                        reply.type = ReplyType.TEXT
                        reply.content = task_data["failReason"]
                        logger.debug("Sent failReason as reply content.")
        else:
            logger.debug("Generating prompt...")
            prompt = generate_prompt(content=prompt) if self.gpt_optimized else prompt
            prompt += commands
            logger.debug(f"Generated prompt: {prompt}")

            logger.debug("Getting imagination data...")
            imagine_data = self.mm.get_imagine(prompt=prompt)
            if isinstance(imagine_data, str):
                reply.type = ReplyType.TEXT
                reply.content = f"任务提交失败，{imagine_data}"
                logger.error(f"Received error message: {imagine_data}")
            else:
                self.send_task_submission_message(e_context, messageId=imagine_data["result"])
                logger.debug(f"Received imagination data: {imagine_data}")
                time.sleep(10)
                logger.debug("Getting image URL...")
                task_data = self.mm.get_image_url(id=imagine_data["result"])
                logger.debug(f"Received task data: {task_data}")
                if isinstance(task_data, str):
                    # 错误信息响应
                    reply.type = ReplyType.TEXT
                    reply.content = task_data
                    logger.error(f"Received error message: {task_data}")
                else:
                    # 正常的JSON响应
                    if task_data["failReason"] is None:
                        com_reply = Reply()
                        com_reply.type = ReplyType.IMAGE
                        if self.split_url:
                            split_url = task_data["imageUrl"].split('/')
                            new_url = '/'.join(split_url[0:3] + split_url[5:])
                        else:
                            new_url = task_data["imageUrl"]
                        short_url = self.get_short_url(short_url_api=self.short_url_api, url=new_url)
                        self.time_diff_start_finish_td, self.time_diff_submit_finish_td = self.get_time_diff(task_data)
                        logger.debug("new_url: %s" % new_url)
                        image_path = download_and_compress_image(new_url, imagine_data['result'])
                        image_storage = open(image_path, 'rb')
                        com_reply.content = image_storage
                        # com_reply.content = task_data["imageUrl"]  # 这里涉及到地址反代的操作，正常主域名反代或没有反代则使用此默认
                        send_with_retry(self.comapp, com_reply, e_context)
                        reply.type = ReplyType.TEXT
                        reply.content = self.complete_prompt.format(id=imagine_data["result"],
                                                                    change_ins=self.change_ins, imgurl=short_url,
                                                                    start_finish=self.time_diff_start_finish_td,
                                                                    submit_finish=self.time_diff_submit_finish_td)
                        logger.debug("Sent image URL and completed prompt.")
                    else:
                        reply.type = ReplyType.TEXT
                        reply.content = task_data["failReason"]
                        logger.debug("Sent failReason as reply content.")
        e_context['reply'] = reply
        e_context.action = EventAction.BREAK_PASS  # 事件结束后，跳过处理context的默认逻辑
        logger.debug("Event action set to BREAK_PASS, reply set.")

    def handle_params_cache(self, e_context, user_id, content, reply):
        if 'image_params' in self.params_cache[user_id]:
            cmsg = e_context['context']['msg']
            logger.debug("params_cache：%s" % self.params_cache)
            logger.debug("user_id in self.params_cache[user_id]")
            img_params = self.params_cache[user_id]
            del self.params_cache[user_id]
            cmsg.prepare()
            base64_data = convert_base64(content)
            base64_data = 'data:image/png;base64,' + base64_data
            imagine_data = self.mm.get_imagine(prompt=img_params['image_params']["prompt"],
                                               base64_data=base64_data)
            if isinstance(imagine_data, str):
                reply.type = ReplyType.TEXT
                reply.content = f"任务提交失败，{imagine_data}"
                logger.error(f"Received error message: {imagine_data}")
            else:
                self.send_task_submission_message(e_context, messageId=imagine_data["result"])
                logger.debug(f"Received imagination data: {imagine_data}")
                time.sleep(10)
                logger.debug("Getting image URL...")
                task_data = self.mm.get_image_url(id=imagine_data["result"])
                logger.debug(f"Received task data: {task_data}")
                if isinstance(task_data, str):
                    # 错误信息响应
                    reply.type = ReplyType.TEXT
                    reply.content = task_data
                    logger.error(f"Received error message: {task_data}")
                else:
                    # 正常的JSON响应
                    if task_data["failReason"] is None:
                        com_reply = Reply()
                        com_reply.type = ReplyType.IMAGE
                        if self.split_url:
                            split_url = task_data["imageUrl"].split('/')
                            new_url = '/'.join(split_url[0:3] + split_url[5:])
                        else:
                            new_url = task_data["imageUrl"]
                        short_url = self.get_short_url(short_url_api=self.short_url_api, url=new_url)
                        self.time_diff_start_finish_td, self.time_diff_submit_finish_td = self.get_time_diff(task_data)
                        logger.debug("new_url: %s" % new_url)
                        image_path = download_and_compress_image(new_url, imagine_data['result'])
                        image_storage = open(image_path, 'rb')
                        com_reply.content = image_storage
                        # com_reply.content = task_data["imageUrl"]  # 这里涉及到地址反代的操作，正常主域名反代或没有反代则使用此默认
                        send_with_retry(self.comapp, com_reply, e_context)
                        reply.type = ReplyType.TEXT
                        reply.content = self.complete_prompt.format(id=imagine_data["result"],
                                                                    change_ins=self.change_ins, imgurl=short_url,
                                                                    start_finish=self.time_diff_start_finish_td,
                                                                    submit_finish=self.time_diff_submit_finish_td)
                        logger.debug("Sent image URL and completed prompt.")
                    else:
                        reply.type = ReplyType.TEXT
                        reply.content = task_data["failReason"]
                        logger.debug("Sent failReason as reply content.")
        elif 'num_pictures' in self.params_cache[user_id]:
            cmsg = e_context['context']['msg']
            logger.debug("params_cache：%s" % self.params_cache)
            logger.debug("user_id in self.params_cache[user_id]")
            cmsg.prepare()
            img_params = self.params_cache[user_id]
            base64_data = convert_base64(content)
            base64_data = 'data:image/png;base64,' + base64_data

            # 将新的base64数据添加到列表中
            img_params['base64_data'].append(base64_data)
            img_params['num_pictures'] -= 1

            # 如果收集到足够数量的图片，调用函数并清除用户数据
            if img_params['num_pictures'] == 0:
                blend_data = self.mm.submit_blend(img_params['base64_data'])
                del self.params_cache[user_id]
                if isinstance(blend_data, str):
                    reply.type = ReplyType.TEXT
                    reply.content = f"任务提交失败，{blend_data}"
                    logger.error(f"Received error message: {blend_data}")
                else:
                    self.send_task_submission_message(e_context, messageId=blend_data["result"])
                    logger.debug(f"Received imagination data: {blend_data}")
                    time.sleep(10)
                    logger.debug("Getting image URL...")
                    task_data = self.mm.get_image_url(id=blend_data["result"])
                    logger.debug(f"Received task data: {task_data}")
                    if isinstance(task_data, str):
                        # 错误信息响应
                        reply.type = ReplyType.TEXT
                        reply.content = task_data
                        logger.error(f"Received error message: {task_data}")
                    else:
                        # 正常的JSON响应
                        if task_data["failReason"] is None:
                            com_reply = Reply()
                            com_reply.type = ReplyType.IMAGE
                            if self.split_url:
                                split_url = task_data["imageUrl"].split('/')
                                new_url = '/'.join(split_url[0:3] + split_url[5:])
                            else:
                                new_url = task_data["imageUrl"]
                            short_url = self.get_short_url(short_url_api=self.short_url_api, url=new_url)
                            self.time_diff_start_finish_td, self.time_diff_submit_finish_td = self.get_time_diff(
                                task_data)
                            logger.debug("new_url: %s" % new_url)
                            image_path = download_and_compress_image(new_url, blend_data['result'])
                            image_storage = open(image_path, 'rb')
                            com_reply.content = image_storage
                            # com_reply.content = task_data["imageUrl"]  # 这里涉及到地址反代的操作，正常主域名反代或没有反代则使用此默认
                            send_with_retry(self.comapp, com_reply, e_context)
                            reply.type = ReplyType.TEXT
                            reply.content = self.complete_prompt.format(id=blend_data["result"],
                                                                        change_ins=self.change_ins, imgurl=short_url,
                                                                        start_finish=self.time_diff_start_finish_td,
                                                                        submit_finish=self.time_diff_submit_finish_td)
                            logger.debug("Sent image URL and completed prompt.")
                        else:
                            reply.type = ReplyType.TEXT
                            reply.content = task_data["failReason"]
                            logger.debug("Sent failReason as reply content.")

    # 定义一个方法，用于生成帮助文本
    def get_help_text(self, verbose=False, **kwargs):
        # 检查配置中是否启用了画图功能
        if not conf().get('image_create_prefix'):
            return "画图功能未启用"  # 如果未启用，则返回提示信息
        else:
            # 否则，获取触发前缀
            trigger = conf()['image_create_prefix'][0]
        # 初始化帮助文本，说明利用 midjourney api 来画图
        help_text = "使用Midjourney来画图，支持垫图、合图、变换等操作\n"
        # 如果不需要详细说明，则直接返回帮助文本
        if not verbose:
            return help_text
        # 否则，添加详细的使用方法到帮助文本中
        help_text += f"使用方法:\n使用\"{trigger}[内容描述]\"的格式作画，如\"{trigger}一个中国漂亮女孩\"\n垫图指令：{trigger} {self.image_ins}，合图指令：{trigger} {self.blend_ins}\n垫图指令后面可以加关键词，合图指令后面不需要加"
        # 返回帮助文本
        return help_text

    def get_short_url(self, short_url_api, url):
        if short_url_api != "":
            # 发送POST请求到short_url_api
            response = requests.post(short_url_api, json={"url": url})
            data = response.json()
            # 拼接得到完整的URL
            short_url = short_url_api + data["key"]
            return short_url
        else:
            return url

    def get_time_diff(self, task_data):
        startTime_sec = task_data['startTime'] / 1000
        finishTime_sec = task_data['finishTime'] / 1000 if task_data['finishTime'] is not None else None
        submitTime_sec = task_data['submitTime'] / 1000

        if finishTime_sec is not None:
            # 计算时间差（以秒为单位）
            time_diff_start_finish = finishTime_sec - startTime_sec
            time_diff_submit_finish = finishTime_sec - submitTime_sec

            # 将时间差转换为时间间隔（timedelta）对象，以便更易于处理
            time_diff_start_finish_td = timedelta(seconds=time_diff_start_finish)
            time_diff_submit_finish_td = timedelta(seconds=time_diff_submit_finish)

            # 计算时间差的秒数
            time_diff_start_finish_td_sec = time_diff_start_finish_td.total_seconds()
            time_diff_submit_finish_td_sec = time_diff_submit_finish_td.total_seconds()
        else:
            time_diff_start_finish_td_sec = None
            time_diff_submit_finish_td_sec = None

        return time_diff_start_finish_td_sec, time_diff_submit_finish_td_sec

    def send_task_submission_message(self, e_context, messageId):
        com_reply = Reply()
        com_reply.type = ReplyType.TEXT
        context = e_context['context']
        if context.kwargs.get('isgroup'):
            msg = context.kwargs.get('msg')
            nickname = msg.actual_user_nickname  # 获取nickname
            com_reply.content = "@{name}\n☑️您的绘图任务提交成功！\n🆔ID：{id}\n⏳正在努力出图，请您耐心等待...".format(
                name=nickname, id=messageId)
        else:
            com_reply.content = "☑️您的绘图任务提交成功！\n🆔ID：{id}\n⏳正在努力出图，请您耐心等待...".format(
                id=messageId)
        self.comapp.send(com_reply, context)
