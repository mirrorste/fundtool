"""环境自检：Python 版本 / akshare 依赖 / 代理环境变量 / 数据源连通性。

退出码：0=全部通过，1=存在失败项；结尾给一句处置建议。
MOCK 模式下连通性失败不计入失败（离线演示不依赖外网）。
"""
import os
import sys
import urllib.request

from . import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# 两个真实数据源页面：200 且响应不含拦截关键词才算通
CHECK_URLS = [
    "https://fund.eastmoney.com/data/rankhandler.aspx",
    "https://fund.eastmoney.com/Fund_JJJZ_Data.aspx",
]
TIMEOUT_S = 5
BLOCK_WORDS = ("访问被拒绝", "上网策略")


def _check_python():
    ok = sys.version_info >= (3, 9)
    detail = sys.version.split()[0] + ("" if ok else "（需 ≥ 3.9）")
    return ok, detail


def _check_akshare():
    try:
        import akshare as ak
        return True, f"akshare {getattr(ak, '__version__', '未知版本')}"
    except Exception as exc:
        return False, (f"akshare 导入失败：{type(exc).__name__}: {exc}"
                       "（pip install akshare==1.18.88）")


def _check_proxy():
    """代理仅作提示，不配置不算失败（无代理网络环境属正常）。"""
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
              "ALL_PROXY", "all_proxy"):
        if os.environ.get(k):
            return f"代理已配置：{k}={os.environ[k]}"
    return "未配置（HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 均未设置；" \
           "如需代理请先 export 对应变量）"


def _check_url(url: str):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://fund.eastmoney.com/",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        status = resp.status
        body = resp.read(65536).decode("utf-8", errors="ignore")
    if status != 200:
        return False, f"{url} → HTTP {status}"
    for w in BLOCK_WORDS:
        if w in body:
            return False, f"{url} → 响应含“{w}”（被网络策略拦截）"
    return True, f"{url} → 200 OK"


def run_doctor() -> int:
    print("财富终端 · 基金 — 环境自检")
    print("=" * 56)
    ok_all = True

    def report(name: str, ok: bool, detail: str):
        nonlocal ok_all
        print(f"{'✅' if ok else '❌'} {name}：{detail}")
        ok_all = ok_all and ok

    report("Python 版本", *_check_python())
    report("依赖 akshare", *_check_akshare())
    print(f"ℹ️  代理环境变量：{_check_proxy()}")
    if config.MOCK:
        print("ℹ️  当前为离线演示模式（config.MOCK=True），使用内置样例数据，"
              "连通性结果仅供参考")

    for url in CHECK_URLS:
        try:
            ok, detail = _check_url(url)
        except Exception as exc:
            ok, detail = False, f"{url} 连接失败：{type(exc).__name__}: {exc}"
        if config.MOCK:
            # mock 不依赖外网：连通性失败只提示、不计失败
            print(f"{'✅' if ok else 'ℹ️'}  连通性：{detail}")
        else:
            report("连通性", ok, detail)

    print("=" * 56)
    if ok_all:
        print("全部检查通过，可直接启动：python -m fundtool --port 8080")
        return 0
    print("存在失败项，处置建议：配置代理后重试（export "
          "HTTPS_PROXY=http://127.0.0.1:7890），或检查 DNS/防火墙；"
          "离线演示可用 --mock 启动。")
    return 1
