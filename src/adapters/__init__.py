# g2b.py (BidPublicInfoService) 는 g2b_opnstd.py로 대체됨.
# 하위호환을 위해 G2BAdapter import는 유지하되, 신규 코드는 G2BOpnStdAdapter 사용.
from .g2b import G2BAdapter          # deprecated: g2b_opnstd.py로 교체 예정
from .g2b_opnstd import G2BOpnStdAdapter

__all__ = ["G2BAdapter", "G2BOpnStdAdapter"]
