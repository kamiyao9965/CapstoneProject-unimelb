# How Konkrd processes health insurance data

The Australian Government publishes private health insurance product data as XML through privatehealth.gov.au. Konkrd ingests this XML and transforms it into a normalised relational database. This database powers Konkrd's product comparison and recommendation engine.

## Fund hierarchy

Australian health insurance has a parent-child fund structure. A parent fund like nib operates multiple whitelabel brands underneath it — AAMI Health Insurance, Qantas Insurance, and others are all nib products sold under different names.

The funds table contains parent funds only (38 funds). Keyed by `FundCode` (e.g., `NIB`, `BUP`, `QCH`). Includes fund name, description, fund type (Open or Restricted), corporate structure, and dependant age limits.

The fund-brands table contains child brands only (44 brands). Each brand has a `BrandCode` (e.g., `NIB01` for Qantas Insurance, `NIB02` for AAMI Health Insurance) and references its parent `FundCode`. Not every fund has child brands — some operate under their own name only.

## Products

The products-master table contains one row per unique product (~1,870 products). Each record identifies the fund, brand, hospital tier, product type (Hospital, Combined, or GeneralHealth), product status, and a link to the source PDF where available.

The products-master-variant table maps each master product to its variants (~133k rows). A single master product might have dozens of variants across states, family types, and excess levels, each with its own `ProductItemID`. `ProductItemID` is the key used by the extras and hospital tables at the variant level.

## Hospital inclusions

The hospital-services-master table is the core of the dataset. One row per product per clinical category (~52k rows). Each of the 38 government-defined categories is marked as Covered, Restricted, or Excluded.

Category names are already normalised to a canonical form (`BackNeckSpine`, `BoneJointMuscle`, `HeartVascularSystem`, etc.). This normalisation is the central challenge for the extraction pipeline — PDFs describe the same category in many different ways ("back, neck, spine" vs "back neck & spine" vs "back and neck"), but they all need to resolve to the same canonical identifier.

## Extras

The extras-master table has one row per product per extras service (~36k rows). Each row records whether the service is covered, its waiting period, and per-person and per-policy limits in AUD.

Limits are not always straightforward. Many products combine the annual limit across multiple services — for example, physiotherapy and chiropractic might share a single $1,000 limit. The limit appears against one service, but the other row is empty. The extras-limit-groups table (~94k rows) resolves this by recording which services share a combined limit and whether sub-limits apply. On the source PDFs, insurers represent this visually by merging table rows.

The extras-benefits table (~1M rows) goes one level deeper, storing per-item benefit amounts within each extras service. For example, under general dental: periodic exam $44, scale and clean $80, fluoride $32. The item codes map to ADA dental item numbers.

## Source PDFs

The government XML contains the structured product data, but not the marketing brochures and fact sheets that consumers actually read. Konkrd separately downloads these PDFs from each insurer's website. Some funds upload their PDFs to the government portal, but it's not mandatory so most don't.

## How the tables connect

One product traced across every table.

Start in products-master. Find the product:

```
ID Master:     1338468388
FundCode:      QCH
BrandCode:     QCH
HospitalTier:  BasicPlus
ProductType:   Combined
Name Master:   Intermediate Hospital (Basic+) excess & Young Extras
```

`ID Master` links to hospital-services-master and extras-master.

Go to funds to see the parent fund:

```
FundCode: QCH
FundName: Queensland Country Health Fund
FundType: Open
```

Go to products-master-variant to find all variants:

```
ProductItemID: 000018d1-da1f-4fdd-ba5b-dc7b3a7779d9
ID Master:     1338468388
```

`ProductItemID` links to extras-limit-groups and extras-benefits. One master product can have many ProductItemIDs — one per state/family/excess combination.

Go to hospital-services-master to see what's covered:

```
ID Master: 1338468388, Title: AssistedReproductive,  Cover: Restricted
ID Master: 1338468388, Title: BackNeckSpine,          Cover: Covered
ID Master: 1338468388, Title: Blood,                  Cover: Covered
ID Master: 1338468388, Title: BoneJointMuscle,        Cover: Covered
ID Master: 1338468388, Title: BrainNervousSystem,     Cover: Covered
...
```

One row per clinical category. Same across all variants of a master product.

Go to extras-master to see the extras:

```
ID Master: 1338468388, Title: Acupuncture,          Covered: True,  WaitingPeriod: 2 Month, LimitPerPolicy: ,    LimitPerPerson:
ID Master: 1338468388, Title: AntenatalPostnatal,    Covered: False
ID Master: 1338468388, Title: Chiropractic,          Covered: True,  WaitingPeriod: 2 Month, LimitPerPolicy: 1000, LimitPerPerson: 500
```

Acupuncture is covered but has no limit values — its limit is shared with another service.

Go to extras-limit-groups to find shared limits:

```
ProductItemID: 000018d1-..., Service: DentalGeneral, Service Combined With: DentalGeneral, Sub Limits Apply: False
```

When `Service` and `Service Combined With` are the same, the limit is standalone. When they differ, the services share a combined limit. To find Acupuncture's actual limit, look for a row where `Service: Acupuncture` and read `Service Combined With` — that tells you which other service holds the dollar figure.

Go to extras-benefits for per-item breakdowns:

```
Title: DentalGeneral, Item: DentalGeneral012PeriodicExam,    Type: Dollars, Quantity: 44
Title: DentalGeneral, Item: DentalGeneral114ScaleClean,      Type: Dollars, Quantity: 80
Title: DentalGeneral, Item: DentalGeneral121Fluoride,        Type: Dollars, Quantity: 32
Title: DentalMajor,   Item: DentalMajor615FullCrownVeneered, Type: Dollars, Quantity: 800
```

The most granular level — how much you get back per specific dental procedure.

## Join structure

```
funds
  └── FundCode → products-master
                   │
fund-brands        ├── ID Master → hospital-services-master
  └── FundCode     ├── ID Master → extras-master
                   │
                   └── products-master-variant
                         │
                         └── ProductItemID → extras-limit-groups
                                           → extras-benefits
```

## Table reference

| Table | Rows | Key columns |
|---|---|---|
| funds | 38 | FundCode, FundName, FundType, CorporateStructure |
| fund-brands | 44 | FundCode, BrandCode, BrandName |
| products-master | 1,870 | ID Master, FundCode, BrandCode, HospitalTier, ProductType, ProductStatus |
| products-master-variant | 133k | ProductItemID, ID Master |
| hospital-services-master | 52k | ID Master, Title, Cover |
| extras-master | 36k | ID Master, Title, Covered, WaitingPeriod, LimitPerPolicy, LimitPerPerson |
| extras-limit-groups | 94k | ProductItemID, Service, Service Combined With, Sub Limits Apply |
| extras-benefits | 1M+ | ProductItemID, Title, Item, Type, Quantity |
