"""结构化相似度原语（液环禁向量铁律）——v2.0 多粒度记忆检索实验用。

复用已蒸馏原语思想：
- NCD 归一化压缩距离（zlib 实现，对应蒸馏 916b51c37b4b）
- 字符级 bigram Jaccard（对应液环 find_duplicates 字符级比对）

零向量、零依赖（stdlib only）。
"""
import zlib

def ncd_sim(a: str, b: str) -> float:
    """NCD 归一化压缩距离转相似度：1 - (C(a+b)-min(Ca,Cb))/max(Ca,Cb)。
    全等→1.0，完全无关→≈0.0。"""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    ca = len(zlib.compress(a.encode("utf-8")))
    cb = len(zlib.compress(b.encode("utf-8")))
    cab = len(zlib.compress((a + "\u0000" + b).encode("utf-8")))
    ncd = (cab - min(ca, cb)) / max(ca, cb)
    return max(0.0, min(1.0, 1.0 - ncd))

def jaccard_bigram_sim(a: str, b: str) -> float:
    """字符级 bigram Jaccard（结构化确定性，禁向量）。"""
    def bigrams(s: str):
        s = "".join(ch for ch in s.lower() if ch.isalnum() or ch in " ")
        return {s[i:i+2] for i in range(len(s) - 1)} if len(s) > 1 else set(s)
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)

def structured_sim(a: str, b: str, mode: str = "ncd") -> float:
    """可选相似度模式：ncd / jaccard / mix（默认 mix 取均值）。"""
    if mode == "ncd":
        return ncd_sim(a, b)
    if mode == "jaccard":
        return jaccard_bigram_sim(a, b)
    return (ncd_sim(a, b) + jaccard_bigram_sim(a, b)) / 2
