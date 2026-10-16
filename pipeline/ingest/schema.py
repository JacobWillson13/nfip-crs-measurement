"""Explicit bronze schema contract for each dataset.

Field names match the OpenFEMA NfipClaims v3 API exactly. Types are pinned here
instead of inferred, so a page full of nulls cannot silently change a column type
and a value that no longer fits its type fails loudly with the field name.
"""

from __future__ import annotations

from datetime import datetime

import pyarrow as pa


class SchemaDriftError(ValueError):
    """Raised when API data no longer matches the pinned schema."""


_ARROW = {
    "string": pa.string(),
    "int64": pa.int64(),
    "float64": pa.float64(),
    "bool": pa.bool_(),
    # Microsecond precision so sentinel dates like 1492-10-12 survive intact.
    "timestamp": pa.timestamp("us", tz="UTC"),
}

NFIP_CLAIMS_FIELDS: dict[str, str] = {
    "id": "int64",
    "asOfDate": "timestamp",
    "dateOfLoss": "timestamp",
    "yearOfLoss": "int64",
    "state": "string",
    "countyCode": "string",
    "censusGeoid": "string",
    "reportedZipCode": "string",
    "latitude": "float64",
    "longitude": "float64",
    "nfipRatedCommunityNumber": "string",
    "nfipCommunityNumberCurrent": "string",
    "nfipCommunityName": "string",
    "crsClassCode": "int64",
    "ratedFloodZone": "string",
    "floodZoneCurrent": "string",
    "elevationDifference": "float64",
    "elevatedBuildingIndicator": "bool",
    "postFIRMConstructionIndicator": "bool",
    "primaryResidenceIndicator": "bool",
    "occupancyType": "int64",
    "policyCount": "int64",
    "originalConstructionDate": "timestamp",
    "originalNBDate": "timestamp",
    "floodEvent": "string",
    "eventDesignationNumber": "string",
    "causeOfDamage": "string",
    "waterDepth": "float64",
    "buildingDamageAmount": "float64",
    "contentsDamageAmount": "float64",
    "amountPaidOnBuildingClaim": "float64",
    "amountPaidOnContentsClaim": "float64",
    "amountPaidOnIncreasedCostOfComplianceClaim": "float64",
    "netBuildingPaymentAmount": "float64",
    "netContentsPaymentAmount": "float64",
    "netIccPaymentAmount": "float64",
    "totalBuildingInsuranceCoverage": "float64",
    "totalContentsInsuranceCoverage": "float64",
    "rateMethod": "string",
}

# Community Status Book v1. Map and program dates arrive as "MM/DD/YY" strings,
# so they stay strings in bronze.
NFIP_COMMUNITY_STATUS_BOOK_FIELDS: dict[str, str] = {
    "communityIdNumber": "string",
    "communityName": "string",
    "county": "string",
    "state": "string",
    "tribal": "bool",
    "participatingInNFIP": "bool",
    "initialFloodHazardBoundaryMap": "timestamp",
    "initialFloodInsuranceRateMap": "timestamp",
    "currentlyEffectiveMapDate": "string",
    "regularEmergencyProgramDate": "string",
    "originalEntryDate": "timestamp",
    "classRatingEffectiveDate": "timestamp",
    "classRating": "int64",
    "sfhaDiscount": "string",
    "nonSfhaDiscount": "string",
    "sanction": "bool",
    "sanctionDate": "string",
    "lastRefresh": "timestamp",
}

SCHEMAS: dict[str, dict[str, str]] = {
    "nfip_claims": NFIP_CLAIMS_FIELDS,
    "nfip_community_status_book": NFIP_COMMUNITY_STATUS_BOOK_FIELDS,
}

# Policies arrive as FEMA's bulk Parquet file, kept untouched in bronze. The contract
# is enforced at registration: these columns must exist or the load is rejected.
NFIP_POLICIES_REQUIRED_COLUMNS: list[str] = [
    "id",
    "propertyState",
    "nfipRatedCommunityNumber",
    "censusGeoid",
    "policyEffectiveDate",
    "policyTerminationDate",
    "cancellationDateOfFloodPolicy",
    "policyCount",
    "crsClassCode",
    "ratedFloodZone",
    "totalBuildingInsuranceCoverage",
    "totalContentsInsuranceCoverage",
    "totalInsurancePremiumOfThePolicy",
]

BULK_REQUIRED_COLUMNS: dict[str, list[str]] = {"nfip_policies": NFIP_POLICIES_REQUIRED_COLUMNS}


def arrow_schema(fields: dict[str, str]) -> pa.Schema:
    return pa.schema([(name, _ARROW[kind]) for name, kind in fields.items()])


def _parse_ts(value):
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


_CASTS = {
    "string": lambda v: str(v),
    "int64": lambda v: int(v),
    "float64": lambda v: float(v),
    "bool": lambda v: bool(v),
    "timestamp": _parse_ts,
}


def check_fields_present(records: list[dict], fields: dict[str, str]) -> None:
    """Fail if a selected field is absent from every record (renamed or dropped upstream)."""
    seen: set[str] = set()
    for r in records:
        seen.update(r.keys())
    missing = [f for f in fields if f not in seen]
    if missing:
        raise SchemaDriftError(f"Fields missing from API response: {missing}")


def to_arrow(records: list[dict], fields: dict[str, str]) -> pa.Table:
    arrays = []
    for name, kind in fields.items():
        cast = _CASTS[kind]
        try:
            values = [None if r.get(name) is None else cast(r[name]) for r in records]
        except (TypeError, ValueError) as exc:
            raise SchemaDriftError(f"Field '{name}' no longer fits type {kind}: {exc}") from exc
        arrays.append(pa.array(values, type=_ARROW[kind]))
    return pa.Table.from_arrays(arrays, schema=arrow_schema(fields))
