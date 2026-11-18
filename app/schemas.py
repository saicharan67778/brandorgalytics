"""Strict public contract, shared by provider adapters and FastAPI."""

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.services.website_extractor import Confidence

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Platform = Literal["Google", "Facebook", "Instagram", "LinkedIn", "X"]
Attribute = Literal[
    "brand name", "main service", "target market", "key strength", "opportunity"
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Request(Model):
    url: Text


class ErrorDetail(Model):
    code: str
    message: str


class ErrorResponse(Model):
    success: Literal[False]
    error: ErrorDetail


class Brand(Model):
    name: Text
    category: Text
    summary: Text


class Competitor(Model):
    name: Text
    website: Text
    strengths: list[Text]
    weaknesses: list[Text]
    source: Literal["found_on_site", "ai_suggestion"]


class Comparison(Model):
    attribute: Attribute
    your_brand: Text
    competitor: Text


class Competition(Model):
    brand_overview: Text
    competitors: Annotated[list[Competitor], Field(min_length=3)]
    strengths: list[Text]
    opportunities: list[Text]
    main_category: Text
    products_services: list[Text]
    competitive_advantages: list[Text]
    positioning_opportunities: list[Text]
    comparison_table: Annotated[list[Comparison], Field(min_length=5, max_length=5)]

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len({row.attribute for row in self.comparison_table}) != 5:
            raise ValueError("Cover all five comparison attributes")
        if len({c.name.casefold() for c in self.competitors}) != len(self.competitors):
            raise ValueError("Competitors must be distinct")
        return self


class Query(Model):
    query: Text
    platforms: Annotated[list[Platform], Field(min_length=1)]
    intent: Text
    relevance: Text = Field(
        description="Explain how this search connects a buyer's need to a specific offering."
    )
    target_audience: Text

    @field_validator("relevance")
    @classmethod
    def explain_relevance(cls, value: str) -> str:
        if value.casefold().rstrip(".") in {
            "low",
            "medium",
            "high",
            "very high",
            "relevant",
        }:
            raise ValueError("Explain the business relevance of the query")
        return value


class Topic(Model):
    type: Literal[
        "landing page",
        "industry page",
        "FAQ",
        "blog topic",
        "location page",
        "business article",
    ]
    topic: Text


class SEO(Model):
    primary_keyword: Text
    secondary_keywords: list[Text]
    recommended_title: Text
    meta_description: Text
    supporting_content: list[Topic]
    long_tail_keywords: list[Text]
    business_keywords: list[Text]
    title_structure: Text


class Service(Model):
    name: Text
    description: Text
    target_industry: Text
    target_customer: Text
    main_benefit: Text
    source: Literal["from_website", "inferred"]


class AudienceSegment(Model):
    audience: Text
    business_type: Text
    requirement: Text


class Audience(Model):
    primary_audience: list[Text]
    secondary_audience: list[Text]
    industries: list[Text]
    customer_needs: list[Text]
    business_types: list[Text]
    customer_profiles: list[Text]
    location_focus: list[Text]
    interests: list[Text]
    recommended_segments: list[Text]
    audience_requirements: list[AudienceSegment]


class Analysis(Model):
    competitor_analysis: Competition
    target_search_queries: Annotated[list[Query], Field(min_length=5, max_length=5)]
    seo_improvements: SEO
    top_services: Annotated[list[Service], Field(min_length=5, max_length=5)]
    target_audience: Audience

    @model_validator(mode="after")
    def cover_platforms(self) -> Self:
        if (
            len({p for query in self.target_search_queries for p in query.platforms})
            != 5
        ):
            raise ValueError("Queries must cover all five platforms")
        if len({q.query.casefold() for q in self.target_search_queries}) != 5:
            raise ValueError("Queries must be distinct")
        return self


class Report(Model):
    brand: Brand
    analysis: Analysis


class Meta(Model):
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    est_cost_usd: float | None
    duration_ms: int
    pages_fetched: int
    content_confidence: Confidence
    evidence_note: str | None = None
    cached: bool


class Response(Report):
    success: Literal[True] = True
    website: str
    meta: Meta
