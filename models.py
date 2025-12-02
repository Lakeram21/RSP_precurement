from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class ProviderResult(BaseModel):
    supplier: str
    part_number: str
    manufacturer: Optional[str] = None
    stock: Optional[int] = None
    price: Optional[float] = None
    url: Optional[str] = None
    exact_match: Optional[bool] = None
    scraped_sku: Optional[str]= None

class SearchResponse(BaseModel):
    query: str
    timestamp: datetime
    results: List[ProviderResult]

class SearchRequest(BaseModel):
    mpn: Optional[str]
    keywords: Optional[str]