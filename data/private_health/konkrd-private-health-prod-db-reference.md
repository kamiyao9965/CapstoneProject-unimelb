# Production Database Reference

## funds.csv

- Row count: 38
- Columns:
  - ID: int (not null)
  - FundID: string (not null)
  - FundItemID: string (not null)
  - Status: string (not null)
  - FundCode: string (not null)
  - FundName: string (not null)
  - FundDescription: string (nullable)
  - FundType: string (not null)
  - Fund RestrictionHint: string (nullable)
  - CorporateStructure: string (nullable)
  - Child Min Age / Child Max Age: int
  - Student Min Age / Student Max Age: int
  - NonStudent Min Age / NonStudent Max Age: int
- First 3 rows:
```json
{"ID": "102", "FundCode": "QTU", "FundName": "TUH, part of the Teachers Health Group", "FundType": "Restricted", "CorporateStructure": "This insurer is a mutual organisation that operates on a not-for-profit basis.", "Child Max Age": "17", "Student Max Age": "31"}
{"ID": "103", "FundCode": "WFD", "FundName": "Westfund Limited", "FundType": "Open", "CorporateStructure": "This insurer is a mutual organisation that operates on a not-for-profit basis.", "Child Max Age": "17", "Student Max Age": "30"}
{"ID": "104", "FundCode": "QCH", "FundName": "Queensland Country Health Fund", "FundType": "Open", "CorporateStructure": "This insurer operates on a not-for-profit basis.", "Child Max Age": "17", "Student Max Age": "31"}
```

## fund-brands.csv

- Row count: 44
- Columns:
  - ID: int (not null)
  - FundCode: string (not null)
  - BrandCode: string (not null)
  - BrandName: string (not null)
  - BrandPhone: string (nullable)
  - BrandEmail: string (nullable)
  - BrandWebsite: string (nullable)
- First 5 rows:
```json
{"ID": "86", "FundCode": "QTU", "BrandCode": "QTU01", "BrandName": "Union Health", "BrandPhone": "1300 661 283", "BrandEmail": "enquiries@unionhealth.com.au", "BrandWebsite": "https://unionhealth.com.au"}
{"ID": "87", "FundCode": "QCH", "BrandCode": "QCH02", "BrandName": "Territory Health Fund", "BrandPhone": "1800 623 893", "BrandEmail": "info@territoryhealth.com.au", "BrandWebsite": "https://www.territoryhealth.com.au/"}
{"ID": "88", "FundCode": "LHS", "BrandCode": "LHS01", "BrandName": "Federation Health", "BrandPhone": "", "BrandEmail": "", "BrandWebsite": ""}
{"ID": "89", "FundCode": "NIB", "BrandCode": "NIB01", "BrandName": "Qantas Insurance", "BrandPhone": "13 49 60", "BrandEmail": "", "BrandWebsite": "https://www.qantasinsurance.com/health"}
{"ID": "90", "FundCode": "NIB", "BrandCode": "NIB02", "BrandName": "AAMI Health Insurance", "BrandPhone": "13 22 44", "BrandEmail": "", "BrandWebsite": "https://www.aami.com.au/health"}
```

## products-master.csv

- Row count: 1,870
- Columns:
  - ID: int (not null)
  - FundCode: string (not null)
  - HospitalTier: string (nullable)
  - ProductType: string (not null)
  - ID Master: int (not null)
  - Name Master: string (not null)
  - ProductStatus: string (not null)
  - Pdf Filepath: string (nullable)
  - Is Apl: string (nullable)
  - BrandCode: string (not null)
- First 5 rows:
```json
{"ID": "3896", "FundCode": "QCH", "HospitalTier": "BasicPlus", "ProductType": "Combined", "ID Master": "1338468388", "Name Master": "Intermediate Hospital (Basic+) excess & Young Extras", "ProductStatus": "closed", "Pdf Filepath": "", "Is Apl": "", "BrandCode": "QCH"}
{"ID": "3897", "FundCode": "MYO", "HospitalTier": "SilverPlus", "ProductType": "Combined", "ID Master": "7792534363", "Name Master": "Silver Plus Advanced Hospital 750 and Base 50% Back Extras", "ProductStatus": "open", "Pdf Filepath": "", "Is Apl": "", "BrandCode": "MYO"}
{"ID": "3898", "FundCode": "PWA", "HospitalTier": "SilverPlus", "ProductType": "Combined", "ID Master": "1220376925", "Name Master": "Silver Plus Advantage & Top Extras", "ProductStatus": "closed", "Pdf Filepath": "", "Is Apl": "", "BrandCode": "PWA"}
{"ID": "3899", "FundCode": "QTU", "HospitalTier": "BronzePlus", "ProductType": "Hospital", "ID Master": "6240364458", "Name Master": "Union Health Bronze+ Hospital excess", "ProductStatus": "open", "Pdf Filepath": "latest-pdfs/QTU/hospital/UH Bronze+ Hospital.pdf", "Is Apl": "", "BrandCode": "QTU01"}
{"ID": "3900", "FundCode": "NIB", "HospitalTier": "SilverPlus", "ProductType": "Hospital", "ID Master": "3731974297", "Name Master": "AAMI Silver Everyday Hospital Plus ", "ProductStatus": "open", "Pdf Filepath": "", "Is Apl": "", "BrandCode": "NIB02"}
```

## products-master-variant.csv

- Row count: 133,469
- Columns:
  - ID: int (not null)
  - ProductItemID: string (not null)
  - ID Master: int (not null)
- First 5 rows:
```json
{"ID": "260693", "ProductItemID": "000018d1-da1f-4fdd-ba5b-dc7b3a7779d9", "ID Master": "1338468388"}
{"ID": "260694", "ProductItemID": "000028e0-8e46-4780-92cc-fdd331dee589", "ID Master": "7792534363"}
{"ID": "260695", "ProductItemID": "0000890c-9110-4efd-95cf-011106307ae6", "ID Master": "1220376925"}
{"ID": "260696", "ProductItemID": "0000fd3c-8a17-4eb6-8517-c0e10d1cc004", "ID Master": "6240364458"}
{"ID": "260697", "ProductItemID": "000123c4-8e9c-4382-bf5f-00b21fcce31b", "ID Master": "3731974297"}
```

## hospital-services-master.csv

- Row count: 52,478
- Columns:
  - ID: int (not null)
  - ID Master: int (not null)
  - Title: string (not null)
  - Cover: string (not null)
- First 5 rows:
```json
{"ID": "117558", "ID Master": "1338468388", "Title": "AssistedReproductive", "Cover": "Restricted"}
{"ID": "117559", "ID Master": "1338468388", "Title": "BackNeckSpine", "Cover": "Covered"}
{"ID": "117560", "ID Master": "1338468388", "Title": "Blood", "Cover": "Covered"}
{"ID": "117561", "ID Master": "1338468388", "Title": "BoneJointMuscle", "Cover": "Covered"}
{"ID": "117562", "ID Master": "1338468388", "Title": "BrainNervousSystem", "Cover": "Covered"}
```

## extras-master.csv

- Row count: 36,261
- Columns:
  - ID: int (not null)
  - ID Master: int (not null)
  - Title: string (not null)
  - Covered: bool (not null)
  - HasSpecialFeatures: bool (not null)
  - WaitingPeriod: int (nullable)
  - WaitingPeriodUnit: string (nullable)
  - AnnualLimit: bool (nullable)
  - LimitPerPolicy: int (nullable)
  - LimitPerPerson: int (nullable)
  - FreeTextLimit: string (nullable)
- First 5 rows:
```json
{"ID": "101500", "ID Master": "1338468388", "Title": "Acupuncture", "Covered": "True", "HasSpecialFeatures": "False", "WaitingPeriod": "2", "WaitingPeriodUnit": "Month", "AnnualLimit": "", "LimitPerPolicy": "", "LimitPerPerson": "", "FreeTextLimit": ""}
{"ID": "101501", "ID Master": "1338468388", "Title": "AntenatalPostnatal", "Covered": "False", "HasSpecialFeatures": "False", "WaitingPeriod": "", "WaitingPeriodUnit": "", "AnnualLimit": "", "LimitPerPolicy": "", "LimitPerPerson": "", "FreeTextLimit": ""}
{"ID": "101502", "ID Master": "1338468388", "Title": "Audiology", "Covered": "False", "HasSpecialFeatures": "False", "WaitingPeriod": "", "WaitingPeriodUnit": "", "AnnualLimit": "", "LimitPerPolicy": "", "LimitPerPerson": "", "FreeTextLimit": ""}
{"ID": "101503", "ID Master": "1338468388", "Title": "ChineseHerbalMedicine", "Covered": "True", "HasSpecialFeatures": "False", "WaitingPeriod": "2", "WaitingPeriodUnit": "Month", "AnnualLimit": "", "LimitPerPolicy": "", "LimitPerPerson": "", "FreeTextLimit": ""}
{"ID": "101504", "ID Master": "1338468388", "Title": "Chiropractic", "Covered": "True", "HasSpecialFeatures": "False", "WaitingPeriod": "2", "WaitingPeriodUnit": "Month", "AnnualLimit": "", "LimitPerPolicy": "1000", "LimitPerPerson": "500", "FreeTextLimit": ""}
```

## extras-limit-groups.csv

- Row count: 93,788
- Columns:
  - ProductItemID: string (not null)
  - Service: string (not null)
  - Service Combined With: string (not null)
  - Sub Limits Apply: bool (not null)
- First 5 rows:
```json
{"ProductItemID": "000018d1-da1f-4fdd-ba5b-dc7b3a7779d9", "Service": "DentalGeneral", "Service Combined With": "DentalGeneral", "Sub Limits Apply": "False"}
{"ProductItemID": "000028e0-8e46-4780-92cc-fdd331dee589", "Service": "DentalGeneral", "Service Combined With": "DentalGeneral", "Sub Limits Apply": "False"}
{"ProductItemID": "0000890c-9110-4efd-95cf-011106307ae6", "Service": "DentalGeneral", "Service Combined With": "DentalGeneral", "Sub Limits Apply": "False"}
{"ProductItemID": "0000bcf5-e487-48ac-8e2a-6cf197aaf1e7", "Service": "DentalGeneral", "Service Combined With": "DentalGeneral", "Sub Limits Apply": "False"}
{"ProductItemID": "00013d4b-46ec-4165-97a7-315a234b9807", "Service": "DentalGeneral", "Service Combined With": "DentalGeneral", "Sub Limits Apply": "True"}
```

## extras-benefits.csv

- Row count: 1,048,575
- Columns:
  - ID: int (not null)
  - ProductItemID: string (not null)
  - Title: string (not null)
  - Benefitslist: string (not null)
  - Item: string (not null)
  - Type: string (not null)
  - Quantity: int (not null)
- First 5 rows:
```json
{"ID": "8935437", "ProductItemID": "a2a80e9c-c4c6-4344-aa37-624c771e04bb", "Title": "DentalGeneral", "Benefitslist": "c75a9dde4d3b4880", "Item": "DentalGeneral012PeriodicExam", "Type": "Dollars", "Quantity": "44"}
{"ID": "8935438", "ProductItemID": "a2a80e9c-c4c6-4344-aa37-624c771e04bb", "Title": "DentalGeneral", "Benefitslist": "c75a9dde4d3b4880", "Item": "DentalGeneral114ScaleClean", "Type": "Dollars", "Quantity": "80"}
{"ID": "8935439", "ProductItemID": "a2a80e9c-c4c6-4344-aa37-624c771e04bb", "Title": "DentalGeneral", "Benefitslist": "c75a9dde4d3b4880", "Item": "DentalGeneral121Fluoride", "Type": "Dollars", "Quantity": "32"}
{"ID": "8935440", "ProductItemID": "a2a80e9c-c4c6-4344-aa37-624c771e04bb", "Title": "DentalGeneral", "Benefitslist": "c75a9dde4d3b4880", "Item": "DentalGeneral322Extraction", "Type": "Dollars", "Quantity": "156"}
{"ID": "8935441", "ProductItemID": "a2a80e9c-c4c6-4344-aa37-624c771e04bb", "Title": "DentalMajor", "Benefitslist": "92fd7cade7d64a4e", "Item": "DentalMajor615FullCrownVeneered", "Type": "Dollars", "Quantity": "800"}
```
