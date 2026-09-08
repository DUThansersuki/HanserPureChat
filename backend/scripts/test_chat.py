import requests

url = "http://127.0.0.1:8765/v1/chat"
conversation_id = "tool-test-001"


def chat(text):
    r = requests.post(
        url,
        json={
            "conversation_id": conversation_id,
            "message": text,
        },
    )

    print("状态码：", r.status_code)

    data = r.json()

    print("用户：", text)
    print("回答：", data["text"])
    print("关键词：", data["keywords"])
    print("锚定：", data["anchored"])
    print("sources数量：", len(data["sources"]))
    print("-" * 60)


chat("恶心心 想到你不就要憨憨的笑了嘛")