"""
赫布关联引擎（Hebbian Association Engine）
基于洞察二：一起激活的记忆单元自动建立关联，关联强度随共现次数增加。
参考D541大脑计算神经回路激活次数 + D527大脑可塑性。
赫布法则：Neurons that fire together, wire together.
"""
import json
import math
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Optional


class HebbianAssociation:
    """赫布关联引擎"""
    
    def __init__(self, association_store_path: str = None, 
                 decay_constant: float = 10.0,
                 shortcut_threshold: float = 0.8,
                 max_associations: int = 10000):
        """
        初始化赫布关联引擎
        :param association_store_path: 关联存储路径
        :param decay_constant: 衰减常数（共激活10次达到0.63强度）
        :param shortcut_threshold: 快捷通路固化阈值（默认0.8）
        :param max_associations: 最大关联数（防止无限增长）
        """
        self.association_store_path = Path(association_store_path) if association_store_path else None
        self.decay_constant = decay_constant
        self.shortcut_threshold = shortcut_threshold
        self.max_associations = max_associations
        self.associations = {}  # pair_id -> association_data
        self._load()
    
    def _load(self):
        """从文件加载关联数据"""
        if self.association_store_path and self.association_store_path.exists():
            try:
                with open(self.association_store_path, "r", encoding="utf-8") as f:
                    self.associations = json.load(f)
            except:
                self.associations = {}
    
    def _save(self):
        """保存关联数据到文件"""
        if self.association_store_path:
            self.association_store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.association_store_path, "w", encoding="utf-8") as f:
                json.dump(self.associations, f, ensure_ascii=False, indent=2)
    
    def _pair_id(self, memory_a: str, memory_b: str) -> str:
        """生成记忆对ID（排序后拼接，保证对称性）"""
        pair = sorted([memory_a, memory_b])
        return f"{pair[0]}-{pair[1]}"
    
    def calculate_strength(self, co_activation_count: int) -> float:
        """
        计算关联强度
        公式：strength = 1 - exp(-count / decay_constant)
        共激活10次→0.63，20次→0.86，30次→0.95
        """
        return 1.0 - math.exp(-co_activation_count / self.decay_constant)
    
    def update_association(self, memory_a: str, memory_b: str, 
                           context: str = "") -> Dict:
        """
        更新一对记忆的关联（共激活一次）
        :param memory_a: 记忆A的ID
        :param memory_b: 记忆B的ID
        :param context: 共现上下文标签
        :return: 更新后的关联数据
        """
        if memory_a == memory_b:
            return {}
        
        pair_id = self._pair_id(memory_a, memory_b)
        
        if pair_id not in self.associations:
            self.associations[pair_id] = {
                "pair_id": pair_id,
                "memory_a": memory_a,
                "memory_b": memory_b,
                "co_activation_count": 0,
                "association_strength": 0.0,
                "first_activated": datetime.now().isoformat(),
                "last_activated": datetime.now().isoformat(),
                "context_tags": [],
                "is_shortcut": False
            }
        
        assoc = self.associations[pair_id]
        assoc["co_activation_count"] += 1
        assoc["association_strength"] = self.calculate_strength(assoc["co_activation_count"])
        assoc["last_activated"] = datetime.now().isoformat()
        
        if context and context not in assoc["context_tags"]:
            assoc["context_tags"].append(context)
        
        # 快捷通路固化
        if assoc["association_strength"] >= self.shortcut_threshold:
            assoc["is_shortcut"] = True
        
        self._save()
        return assoc
    
    def update_from_activation(self, activated_ids: List[str], 
                                context: str = "") -> int:
        """
        从一次激活事件中更新所有关联
        :param activated_ids: 本次激活的记忆ID列表
        :param context: 上下文标签
        :return: 更新的关联数量
        """
        count = 0
        for i in range(len(activated_ids)):
            for j in range(i + 1, len(activated_ids)):
                self.update_association(activated_ids[i], activated_ids[j], context)
                count += 1
        return count
    
    def get_associations(self, memory_id: str, 
                          min_strength: float = 0.0,
                          limit: int = 20) -> List[Dict]:
        """
        获取某个记忆的所有关联（按强度排序）
        """
        results = []
        for pair_id, assoc in self.associations.items():
            if assoc["memory_a"] == memory_id or assoc["memory_b"] == memory_id:
                if assoc["association_strength"] >= min_strength:
                    other = assoc["memory_b"] if assoc["memory_a"] == memory_id else assoc["memory_a"]
                    results.append({
                        "other_memory": other,
                        "strength": assoc["association_strength"],
                        "co_activation_count": assoc["co_activation_count"],
                        "is_shortcut": assoc["is_shortcut"],
                        "context_tags": assoc["context_tags"]
                    })
        results.sort(key=lambda x: x["strength"], reverse=True)
        return results[:limit]
    
    def get_shortcuts(self) -> List[Dict]:
        """获取所有快捷通路（强度>阈值的关联）"""
        return [a for a in self.associations.values() if a.get("is_shortcut", False)]
    
    def decay_weak_associations(self, days_threshold: int = 30,
                                  min_strength: float = 0.1) -> int:
        """
        衰减弱关联（防马太效应）
        超过days_threshold天未激活且强度<min_strength的关联被清理
        """
        to_remove = []
        now = datetime.now()
        for pair_id, assoc in self.associations.items():
            last = datetime.fromisoformat(assoc["last_activated"])
            days = (now - last).days
            if days > days_threshold and assoc["association_strength"] < min_strength:
                to_remove.append(pair_id)
        
        for pair_id in to_remove:
            del self.associations[pair_id]
        
        if to_remove:
            self._save()
        
        return len(to_remove)
    
    def get_stats(self) -> Dict:
        """获取关联引擎统计信息"""
        total = len(self.associations)
        shortcuts = sum(1 for a in self.associations.values() if a.get("is_shortcut", False))
        avg_strength = sum(a["association_strength"] for a in self.associations.values()) / total if total else 0
        return {
            "total_associations": total,
            "shortcut_count": shortcuts,
            "average_strength": round(avg_strength, 3),
            "decay_constant": self.decay_constant,
            "shortcut_threshold": self.shortcut_threshold
        }


# 便捷函数
def update_hebbian_associations(activated_ids: List[str], 
                                  store_path: str = None,
                                  context: str = "") -> Dict:
    """便捷函数：从一次激活事件更新赫布关联"""
    engine = HebbianAssociation(association_store_path=store_path)
    count = engine.update_from_activation(activated_ids, context)
    return {"updated": count, "stats": engine.get_stats()}


if __name__ == "__main__":
    # 简单测试
    engine = HebbianAssociation()
    # 模拟5次共激活
    for _ in range(5):
        engine.update_from_activation(["D539", "D541", "D543"], "生成式检索")
    print(json.dumps(engine.get_stats(), ensure_ascii=False, indent=2))
    print("\nD539的关联:")
    for a in engine.get_associations("D539"):
        print(f"  {a['other_memory']}: 强度={a['strength']:.2f}, 共激活={a['co_activation_count']}次")
