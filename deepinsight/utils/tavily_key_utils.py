import os
import requests
import logging


# Tavily API 请求函数
def get_key_usage(api_key):
    url = "https://api.tavily.com/usage"
    headers = {'Authorization': f'Bearer {api_key}'}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        # 解析plan_limit和plan_usage
        plan_limit = data.get('account', {}).get('plan_limit', None)
        plan_usage = data.get('account', {}).get('plan_usage', None)

        if plan_limit is None or plan_usage is None:
            raise ValueError("Invalid response structure, plan_limit or plan_usage is missing.")

        return plan_limit, plan_usage

    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed for API Key {api_key}: {e}")
        return None, None
    except ValueError as e:
        logging.error(f"Error parsing response for API Key {api_key}: {e}")
        return None, None


def ensure_api_key_available(api_key: str, min_limit: int):
    """
    检查传入的 key 是否满足 min_limit 的剩余额度，
    如果不足，则自动调用 select_api_key 获取一个新的 key。
    如果仍然无法找到，则返回 False。
    """

    # 先检查当前 key 的使用情况
    plan_limit, plan_usage = get_key_usage(api_key)

    if plan_limit is not None and plan_usage is not None:
        remaining = plan_limit - plan_usage
        if remaining >= min_limit:
            logging.info(f"Current API key is valid. Remaining: {remaining}")
            return api_key  # 当前 key 足够使用
        else:
            logging.warning(f"Current API key insufficient. Remaining: {remaining}, required: {min_limit}")
    else:
        logging.warning("Failed to read usage for current API key.")

    # 如果不足，则调用 select_api_key
    new_key, _ = select_api_key(min_limit=min_limit)

    if new_key:
        logging.info(f"Using new selected API key: {new_key}")
        os.environ['TAVILY_API_KEY'] = new_key
        return new_key

    logging.error("No API key available after selection.")
    return False


# 选择最合适的API key
def select_api_key(min_limit: int = 400):
    # 从环境变量中读取 API Keys
    api_keys = os.environ.get('TAVILY_API_KEYS', '').split(',')

    # 如果没有配置 API Keys，跳过
    if not api_keys or api_keys == ['']:
        logging.info("No API keys found in environment variable, skipping process.")
        return None, {}

    selected_key = None
    key_usage_map = {}

    # 查询每个API key的使用情况并记录
    for key in api_keys:
        plan_limit, plan_usage = get_key_usage(key)
        key_usage_map[key] = {"plan_limit": plan_limit, "plan_usage": plan_usage}

    # 首先选择 plan_limit - plan_usage > 400 的 key
    for key, usage in key_usage_map.items():
        plan_limit, plan_usage = usage["plan_limit"], usage["plan_usage"]
        if plan_limit is not None and plan_usage is not None:
            if plan_limit - plan_usage > min_limit:
                logging.info(f"API Key: {key} - Plan Limit: {plan_limit}, Plan Usage: {plan_usage}")
                selected_key = key
                break

    # 如果没有找到大于 400 的余量，选择余量 >= 200 的 key
    if not selected_key:
        for key, usage in key_usage_map.items():
            plan_limit, plan_usage = usage["plan_limit"], usage["plan_usage"]
            if plan_limit is not None and plan_usage is not None:
                if plan_limit - plan_usage >= min_limit - 50:
                    logging.info(f"API Key: {key} - Plan Limit: {plan_limit}, Plan Usage: {plan_usage}")
                    selected_key = key
                    break

    # 如果还是没有选择到，选择第一个可用的
    if not selected_key:
        for key, usage in key_usage_map.items():
            plan_limit, plan_usage = usage["plan_limit"], usage["plan_usage"]
            if plan_limit is not None and plan_usage is not None:
                logging.info(f"API Key: {key} - Plan Limit: {plan_limit}, Plan Usage: {plan_usage}")
                selected_key = key
                break

    # 如果找到了有效的key，更新环境变量
    if selected_key:
        os.environ['TAVILY_API_KEY'] = selected_key
        logging.info(f"Selected API Key: {selected_key}")
        return selected_key, key_usage_map
    else:
        logging.error("No valid API key found")
        return None, key_usage_map


# 使用示例
if __name__ == "__main__":
    selected_key, all_keys_usage = select_api_key()

    if selected_key:
        print(f"Selected API Key: {selected_key}")
    else:
        print("No valid API key was selected.")

    print("All API Keys Usage:")
    for key, usage in all_keys_usage.items():
        print(f"API Key: {key} - Plan Limit: {usage['plan_limit']}, Plan Usage: {usage['plan_usage']}")
