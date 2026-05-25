from moomoo import OpenQuoteContext


class FutuClient:
    """单例，整个扫描过程复用同一连接"""

    def __init__(self, host="127.0.0.1", port=11111):
        self.ctx = OpenQuoteContext(host=host, port=port)

    def close(self):
        self.ctx.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
