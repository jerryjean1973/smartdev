"""Shared stateless transport for OpenAI and Claude inference."""
import re
import time

SECRET = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:gh[pousr]_|github_pat_|sk-|hvs\.)[A-Za-z0-9_-]{12,}|(?:api[_-]?key|client[_-]?secret|password|token)\s*[:=]\s*[\x22\x27][A-Za-z0-9_+/=-]{12,}[\x22\x27]", re.I)


class ProviderFailure(Exception):
    def __init__(self,message, uncertain=False):
        super().__init__(message)
        self.uncertain=uncertain


def run_model(provider, model, key, instructions, prompt, *, limit=256, structured=False, transport=None):
    if transport is None:
        import httpx as transport
    if provider not in ("openai","anthropic"):
        raise ProviderFailure("Unsupported inference provider.")
    if provider=="anthropic":
        url="https://api.anthropic.com/v1/messages"
        headers={"x-api-key":key,"anthropic-version":"2023-06-01"}
        body={"model":model,"max_tokens":limit,"system":instructions,"messages":[{"role":"user","content":prompt}]}
    else:
        url="https://api.openai.com/v1/responses"
        headers={"Authorization":"Bearer "+key}
        body={"model":model,"instructions":instructions,"input":prompt,"store":False,"max_output_tokens":limit}
    if structured and provider=="openai":
        body["text"]={"format":{"type":"json_object"}}
    with transport.Client(timeout=transport.Timeout(48,connect=6),follow_redirects=False,trust_env=False) as client:
        for attempt in range(2):
            try:
                response=client.post(url,headers=headers,json=body)
            except (transport.ConnectError,transport.ConnectTimeout):
                if attempt==0:
                    time.sleep(.5)
                    continue
                raise ProviderFailure(f"{provider} is unreachable.") from None
            except transport.RequestError:
                raise ProviderFailure(f"{provider} request outcome is unknown.",True) from None
            if response.status_code==429 and attempt==0:
                time.sleep(1)
                continue
            if response.status_code>=500:
                raise ProviderFailure(f"{provider} returned a service error.",True)
            if response.status_code!=200:
                raise ProviderFailure(f"{provider} rejected the request (HTTP {response.status_code}).")
            break
    try:
        reply=response.json()
        if provider=="anthropic":
            content=reply["content"]
        else:
            content=[part for item in reply["output"] if item["type"]=="message" for part in item["content"]]
        output="".join(part["text"] for part in content if part["type"] in ("text","output_text"))
        if not output.strip() or key in output or SECRET.search(output):
            raise ProviderFailure("Provider returned no usable text.")
        usage={key:int(value) for key,value in reply.get("usage",{}).items()
            if key in ("input_tokens","output_tokens","cache_creation_input_tokens","cache_read_input_tokens") and isinstance(value,int)}
        if not all(name in usage and usage[name]>=0 for name in ("input_tokens","output_tokens")):
            raise ProviderFailure("Provider returned incomplete usage.")
        return output,usage
    except (KeyError,ValueError,TypeError):
        raise ProviderFailure(f"{provider} returned an invalid inference response.") from None
