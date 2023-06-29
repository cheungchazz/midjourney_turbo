import json
import time

import requests

from common.log import logger


class MidJourneyModule:
    def __init__(self, api_key, domain_name):
        self.api_key = api_key
        self.domain_name = domain_name

    def get_imagine(self, prompt, base64_data=None):  # 提交出图或垫图
        data = {"base64": base64_data, "prompt": prompt}
        # logger.debug(f"get_imagine data:{data}")
        api_url = f"{self.domain_name}/mj/submit/imagine"

        headers = {
            "mj-api-secret": self.api_key
        }

        response = requests.post(url=api_url, headers=headers, json=data, timeout=120.05)
        if response.status_code == 200:
            get_imagine_data = response.json()
            logger.debug("get_imagine_data: %s" % get_imagine_data)
            # 判断code是否为1，如果不是，返回description
            if get_imagine_data.get('code') != 1:
                return get_imagine_data.get('description')
            else:
                return get_imagine_data  # 返回提交任务结果
        else:
            logger.error("Error occurred: %s" % response.text)
            return "哦豁，出现了未知错误，请联系管理员~~~"

    def get_image_url(self, id):  # 查询任务获取进度
        api_url = f"{self.domain_name}/mj/task/{id}/fetch"
        headers = {
            "mj-api-secret": self.api_key
        }

        start_time = time.time()  # 记录开始时间
        while True:
            response = requests.get(url=api_url, headers=headers, timeout=120.05)
            if response.status_code == 200:
                get_image_url_data = response.json()
                logger.debug("get_image_url_data: %s" % get_image_url_data)
                if get_image_url_data['failReason'] is None:
                    if get_image_url_data['status'] != 'SUCCESS':
                        time.sleep(30)
                        # 检查是否超过120秒
                        if time.time() - start_time > 300:
                            return "请求超时，请稍后再试~~~"
                    else:
                        return get_image_url_data
                else:
                    return get_image_url_data
            else:
                logger.error("Error occurred: %s" % response.text)
                return "哦豁，出现了未知错误，请联系管理员~~~"

    def get_simple(self, content):  # 提交变换任务
        data = {"content": content}
        logger.debug("data: %s" % data)
        api_url = f"{self.domain_name}/mj/submit/simple-change"

        headers = {
            "mj-api-secret": self.api_key
        }

        response = requests.post(url=api_url, headers=headers, json=data, timeout=120.05)
        if response.status_code == 200:
            get_imagine_data = response.json()
            logger.debug("get_imagine_data: %s" % get_imagine_data)
            return get_imagine_data
        else:
            logger.error("Error occurred: %s" % response.text)
            return "哦豁，出现了未知错误，请联系管理员~~~"

    def submit_blend(self, base64_data, dimensions="SQUARE"):
        assert isinstance(base64_data, list) and 2 <= len(
            base64_data) <= 4, "base64_data should be a list with 2 to 4 items."

        url = f"{self.domain_name}/mj/submit/blend"
        headers = {"Content-Type": "application/json", "mj-api-secret": self.api_key}
        data = {
            "base64Array": base64_data,
            "dimensions": dimensions,  # dimensions 比例: PORTRAIT(2:3); SQUARE(1:1); LANDSCAPE(3:2)
            "notifyHook": "",
            "state": ""
        }

        response = requests.post(url, headers=headers, data=json.dumps(data))
        if response.status_code == 200:
            get_imagine_data = response.json()
            logger.debug("get_imagine_data: %s" % get_imagine_data)
            return get_imagine_data
        else:
            logger.error("Error occurred: %s" % response.text)
            return "哦豁，出现了未知错误，请联系管理员~~~"


