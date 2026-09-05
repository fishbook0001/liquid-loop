"""
生成式记忆检索（Generative Memory Retrieval）
基于洞察一：液环记忆召回应是"线索激活→稀疏激活→生成整合"，而非精确ID查找。
参考D539加州理工想象力编码：想象时40%神经元重新激活，想象=感知的神经再次触发。
"""
import json
import math
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Optional
from collections import Counter

# 汉字连续段（CJK 统一表意文字），用于零依赖中文切分
_CJK_SEG = re.compile(r"[\u4e00-\u9fff]+")


def _cjk_bigrams(text: str) -> List[str]:
    """中文字符 bigram 切分（零依赖；禁向量铁律下的最小可用中文相关度近似）。

    为什么需要它（2026-09-03 定因，勿删）：
    原实现是「整词子串匹配」——`encode_cue` 按空格/标点切词，`calculate_activation`
    判断 `kw in memory_text`。英文成立；**中文没有空格**，cue 被切成整块
    （"赫布法则"、"液环记忆"），而记忆里写作"赫布关联"、"液环用"，整词永远匹配
    不上 → `activation_scores` 恒为 0.0，生成式检索的相关性排序实际退化为
    「随机取 top ratio」。返回值格式正常、无任何报错 —— 标准沉默失效。
    文件末尾 `__main__` 自测之所以"通过"，是因为它恰好让 cue 命中了 tags 里的
    完整词，掩盖了缺陷。

    bigram 把匹配粒度降到字符对：中文只要部分词序重合即可命中（"液环"/"赫布"），
    是零依赖前提下代价最小的修法（不引入分词器、不引入向量）。
    """
    grams = []
    for seg in _CJK_SEG.findall(text or ""):
        if len(seg) == 1:
            grams.append(seg)
            continue
        for i in range(len(seg) - 1):
            grams.append(seg[i:i + 2])
    return grams


class GenerativeRecall:
    """生成式记忆检索引擎"""
    
    def __init__(self, memory_store_path: str = None, activation_ratio: float = 0.4,
                 cjk_weight: float = 1.0):
        """
        初始化生成式检索引擎
        :param memory_store_path: 记忆存储路径
        :param activation_ratio: 激活比例（默认0.4，参考D539的40%神经元重新激活）
        :param cjk_weight: 中文 bigram 覆盖率项的权重（默认1.0，与整词命中同量级）
        """
        self.memory_store_path = Path(memory_store_path) if memory_store_path else None
        self.activation_ratio = activation_ratio
        self.cjk_weight = cjk_weight
        self.activation_history = []  # 激活历史，用于赫布关联更新
    
    def encode_cue(self, cue: str) -> Dict[str, float]:
        """
        线索编码层：将输入线索编码为语义权重向量
        零依赖实现：用简单的关键词匹配和TF-IDF，不引入向量数据库
        """
        # 简单分词（按空格和标点）
        words = cue.lower().replace(",", " ").replace(".", " ").replace("，", " ").split()
        # 去除停用词
        stop_words = {"的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这"}
        keywords = [w for w in words if w not in stop_words and len(w) > 1]
        # 计算词频权重
        weights = {}
        for kw in keywords:
            weights[kw] = weights.get(kw, 0) + 1.0
        # 归一化
        total = sum(weights.values()) if weights else 1.0
        return {k: v / total for k, v in weights.items()}
    
    def calculate_activation(self, memory: Dict, cue_weights: Dict[str, float],
                             cue_grams: Optional[Counter] = None) -> float:
        """
        稀疏激活层：计算单个记忆单元的激活分数
        两路打分相加：
          ① 整词/连续短语子串匹配（英文词、精确短语，命中即高分）
          ② 中文 bigram 覆盖率 0~1（补足中文无空格导致 ① 的结构性失效）
        """
        # tags 可能是 list，原实现直接做字符串加法会 TypeError → 统一 str()
        memory_text = (str(memory.get("title", "")) + " "
                       + str(memory.get("content", "")) + " "
                       + str(memory.get("tags", ""))).lower()
        score = 0.0
        # ① 整词/连续短语匹配
        for kw, weight in cue_weights.items():
            if kw in memory_text:
                # 关键词出现次数越多，激活越强（对数增长防止过度强化）
                count = memory_text.count(kw)
                score += weight * (1 + math.log(count + 1))
        # ② 中文字符 bigram 覆盖率：按 cue 侧 gram 总数归一，避免长文本占便宜
        if cue_grams:
            mgrams = Counter(_cjk_bigrams(memory_text))
            if mgrams:
                hit = sum(min(c, mgrams[g]) for g, c in cue_grams.items())
                score += self.cjk_weight * hit / sum(cue_grams.values())
        return score
    
    def sparse_activate(self, memories: List[Dict], cue_weights: Dict[str, float], 
                        ratio: float = None, cue_grams: Optional[Counter] = None
                        ) -> List[Tuple[Dict, float]]:
        """
        稀疏激活：取top ratio比例的记忆激活
        :param ratio: 激活比例，默认用self.activation_ratio（0.4）
        :param cue_grams: cue 的中文 bigram 计数（可选，中文打分用）
        """
        if ratio is None:
            ratio = self.activation_ratio
        # 计算所有记忆的激活分数
        scored = [(m, self.calculate_activation(m, cue_weights, cue_grams))
                  for m in memories]
        # 按分数排序
        scored.sort(key=lambda x: x[1], reverse=True)
        # 取top ratio
        n_activate = max(1, int(len(scored) * ratio))
        activated = scored[:n_activate]
        # 记录激活历史（用于赫布关联更新）
        activated_ids = [m.get("id", "") for m, _ in activated if m.get("id")]
        if activated_ids:
            self.activation_history.append({
                "timestamp": datetime.now().isoformat(),
                "cue": list(cue_weights.keys()),
                "activated_ids": activated_ids
            })
        return activated
    
    def generate_integration(self, activated: List[Tuple[Dict, float]], 
                              cue: str) -> str:
        """
        生成整合层：将激活的记忆内容整合为连贯输出
        零依赖实现：用模板生成，不调用LLM
        """
        if not activated:
            return f"未找到与'{cue}'相关的记忆。"
        
        # 按激活强度排序
        activated.sort(key=lambda x: x[1], reverse=True)
        
        # 生成整合内容
        parts = [f"基于线索'{cue}'，激活了{len(activated)}条相关记忆：\n"]
        for i, (memory, score) in enumerate(activated[:5], 1):
            title = memory.get("title", "未命名")
            content = memory.get("content", "")[:200]
            parts.append(f"{i}. [{title}]（激活强度：{score:.2f}）\n   {content}\n")
        
        if len(activated) > 5:
            parts.append(f"... 还有{len(activated) - 5}条相关记忆未展开。\n")
        
        # 生成关联洞察
        if len(activated) >= 2:
            parts.append("\n【关联洞察】\n")
            parts.append(f"以上{len(activated)}条记忆在'{cue}'线索下共同激活，")
            parts.append("可能存在跨领域关联。建议进一步分析这些记忆之间的赫布关联强度。\n")
        
        return "".join(parts)
    
    def recall(self, cue: str, memories: List[Dict], 
               generate: bool = True) -> Dict:
        """
        完整的生成式检索流程
        :param cue: 检索线索
        :param memories: 记忆列表
        :param generate: 是否生成整合内容
        :return: 检索结果（激活记忆列表 + 生成内容 + 激活映射）
        """
        # 1. 线索编码
        cue_weights = self.encode_cue(cue)
        cue_grams = Counter(_cjk_bigrams(cue))     # 中文 bigram，补整词匹配之不足
        # 2. 稀疏激活
        activated = self.sparse_activate(memories, cue_weights, cue_grams=cue_grams)
        # 3. 生成整合
        generated = self.generate_integration(activated, cue) if generate else ""
        # 4. 激活映射
        activation_map = {m.get("id", ""): score for m, score in activated if m.get("id")}
        
        return {
            "cue": cue,
            "cue_weights": cue_weights,
            "activated_memories": [m for m, _ in activated],
            "activation_scores": {m.get("id", ""): score for m, score in activated},
            "generated_content": generated,
            "activation_map": activation_map,
            "activation_ratio": self.activation_ratio,
            "timestamp": datetime.now().isoformat()
        }


# 便捷函数
def generative_recall(cue: str, memories: List[Dict], 
                      activation_ratio: float = 0.4) -> Dict:
    """便捷函数：执行生成式检索"""
    engine = GenerativeRecall(activation_ratio=activation_ratio)
    return engine.recall(cue, memories)


if __name__ == "__main__":
    # 简单测试
    test_memories = [
        {"id": "D539", "title": "加州理工破解大脑想象力编码", "content": "想象时40%神经元重新激活，想象=感知的神经再次触发", "tags": "认知神经科学 想象力"},
        {"id": "D541", "title": "大脑计算神经回路激活次数", "content": "赫布法则：一起激活的神经元连在一起，反复激活强化连接", "tags": "认知神经科学 赫布法则"},
        {"id": "D543", "title": "大脑永不衰老", "content": "海马体神经元再生，激活再生记忆评分提升45%", "tags": "认知神经科学 抗衰"},
    ]
    result = generative_recall("记忆检索 赫布法则", test_memories)
    print(result["generated_content"])
