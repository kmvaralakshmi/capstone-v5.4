"""
PHASE 3 - BRSR SOURCE MANIFEST
==============================

Target:
    FY 2024-25

Company set:
    50 target companies

Source priority:
    1. NSE
    2. BSE
    3. Company Official Website

Rules:
    - One company + one financial year = one active PDF.
    - Never replace a valid canonical PDF.
    - Failed sources move to the next source.
    - Mphasis has an official standalone BRSR fallback.
    - LTIMindtree has its verified NSE Annual Report fallback.
    - Hexaware remains in the 50-company target set.
"""

import csv
from pathlib import Path

BRSR_MANIFEST = {

    # ========================================================
    # 1. TCS
    # ========================================================

    "TCS": {
        "company_name": "Tata Consultancy Services",
        "aliases": [
            "tata consultancy services",
            "tata consultancy services limited",
            "tcs"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": (
                    "https://nsearchives.nseindia.com/"
                    "corporate/"
                    "TCS_CORPCS_27052025233643_"
                    "BRSRSEint27052025_signed.pdf"
                )
            }
        ]
    },

    # ========================================================
    # 2. INFOSYS
    # ========================================================

    "INFY": {
        "company_name": "Infosys",
        "aliases": [
            "infosys",
            "infosys limited"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": (
                    "https://nsearchives.nseindia.com/"
                    "corporate/"
                    "Infosys_02062025172921_infosys-ar-2025.pdf"
                )
            }
        ]
    },

    # ========================================================
    # 3. WIPRO
    # ========================================================

    "WIPRO": {
        "company_name": "Wipro",
        "aliases": [
            "wipro",
            "wipro limited"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            },
            {
                "priority": 2,
                "name": "BSE",
                "url": None
            },
            {
                "priority": 3,
                "name": "Wipro Official",
                "url": (
                    "https://www.wipro.com/content/dam/"
                    "nexus/en/investor/annual-reports/"
                    "2024-2025/"
                    "Integrated-annual-report-2024-25.pdf"
                )
            }
        ]
    },

    # ========================================================
    # 4. HCL TECHNOLOGIES
    # ========================================================

    "HCLTECH": {
        "company_name": "HCL Technologies",
        "aliases": [
            "hcl technologies",
            "hcl technologies limited",
            "hcltech"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            }
        ]
    },

    # ========================================================
    # 5. TECH MAHINDRA
    # ========================================================

    "TECHM": {
        "company_name": "Tech Mahindra",
        "aliases": [
            "tech mahindra",
            "tech mahindra limited",
            "techm"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": (
                    "https://nsearchives.nseindia.com/"
                    "corporate/"
                    "Apekshakhemka_23062025233414_"
                    "StockExchangeIntimationBRSR_S.pdf"
                )
            }
        ]
    },

    # ========================================================
    # 6. LTIMINDTREE
    # ========================================================

    "LTIM": {
        "company_name": "LTIMindtree",
        "aliases": [
            "ltimindtree",
            "ltimindtree limited",
            "lti mindtree",
            "lti"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE BRSR",
                "url": None
            },
            {
                "priority": 2,
                "name": "NSE Annual Report",
                "url": (
                    "https://nsearchives.nseindia.com/"
                    "annual_reports/"
                    "AR_26433_LTIM_2024_2025_A_"
                    "07052025121730.pdf"
                )
            },
            {
                "priority": 3,
                "name": "BSE",
                "url": None
            },
            {
                "priority": 4,
                "name": "LTIMindtree Official",
                "url": None
            }
        ]
    },

    # ========================================================
    # 7. MPHASIS
    # ========================================================

    "MPHASIS": {
        "company_name": "Mphasis",
        "aliases": [
            "mphasis",
            "mphasis limited",
            "mphasis ltd"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE Annual Report",
                "url": (
                    "https://nsearchives.nseindia.com/"
                    "annual_reports/"
                    "AR_26670_MPHASIS_2024_2025_A_"
                    "01072025190655.pdf"
                )
            },
            {
                "priority": 2,
                "name": "Mphasis Official BRSR",
                "url": (
                    "https://www.mphasis.com/"
                    "content/dam/mphasis-com/global/en/"
                    "investors/annual-reports/2025/"
                    "business-responsibility-report-2025.pdf"
                )
            }
        ]
    },

    # ========================================================
    # 8. PERSISTENT SYSTEMS
    # ========================================================

    "PERSISTENT": {
        "company_name": "Persistent Systems",
        "aliases": [
            "persistent systems",
            "persistent systems limited",
            "persistent"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": (
                    "https://nsearchives.nseindia.com/"
                    "corporate/"
                    "PERSISTENTUSER1_23062025235235_"
                    "PSLBusinessResponsibilityandSustainability"
                    "Report2025signed.pdf"
                )
            }
        ]
    },

    # ========================================================
    # 9. COFORGE
    # ========================================================

    "COFORGE": {
        "company_name": "Coforge",
        "aliases": [
            "coforge",
            "coforge limited",
            "coforge ltd"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            },
            {
                "priority": 2,
                "name": "BSE",
                "url": None
            },
            {
                "priority": 3,
                "name": "Coforge Official",
                "url": None
            }
        ]
    },

    # ========================================================
    # 10. LTTS
    # ========================================================

    "LTTS": {
        "company_name": "L&T Technology Services",
        "aliases": [
            "l&t technology services",
            "l&t technology services limited",
            "l&t technology",
            "ltts"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            }
        ]
    },

    # ========================================================
    # 11. TATA ELXSI
    # ========================================================

    "TATAELXSI": {
        "company_name": "Tata Elxsi",
        "aliases": [
            "tata elxsi",
            "tata elxsi limited",
            "tataelxsi"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            }
        ]
    },

    # ========================================================
    # 12. OFSS
    # ========================================================

    "OFSS": {
        "company_name": "Oracle Financial Services Software",
        "aliases": [
            "oracle financial services software",
            "oracle financial services",
            "ofss"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            }
        ]
    },

    # ========================================================
    # 13. KPIT TECHNOLOGIES
    # ========================================================

    "KPITTECH": {
        "company_name": "KPIT Technologies",
        "aliases": [
            "kpit technologies",
            "kpit technologies limited",
            "kpit"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            }
        ]
    },

    # ========================================================
    # 14. CYIENT
    # ========================================================

    "CYIENT": {
        "company_name": "Cyient",
        "aliases": [
            "cyient",
            "cyient limited"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            }
        ]
    },

    # ========================================================
    # 15. BIRLASOFT
    # ========================================================

    "BSOFT": {
        "company_name": "Birlasoft",
        "aliases": [
            "birlasoft",
            "birlasoft limited",
            "birlasoft ltd"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            }
        ]
    },

    # ========================================================
    # 16. ZENSAR TECHNOLOGIES
    # ========================================================

    "ZENSARTECH": {
        "company_name": "Zensar Technologies",
        "aliases": [
            "zensar technologies",
            "zensar technologies limited",
            "zensar"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            }
        ]
    },

    # ========================================================
    # 17. TATA TECHNOLOGIES
    # ========================================================

    "TATATECH": {
        "company_name": "Tata Technologies",
        "aliases": [
            "tata technologies",
            "tata technologies limited"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": (
                    "https://nsearchives.nseindia.com/"
                    "corporate/"
                    "TTLNSE_28052025200521_"
                    "BRSR.pdf"
                )
            }
        ]
    },

    # ========================================================
    # 18. HAPPIEST MINDS
    # ========================================================

    "HAPPSTMNDS": {
        "company_name": "Happiest Minds Technologies",
        "aliases": [
            "happiest minds",
            "happiest minds technologies",
            "happiest minds technologies limited"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            },
            {
                "priority": 2,
                "name": "BSE",
                "url": None
            },
            {
                "priority": 3,
                "name": "Happiest Minds Official",
                "url": (
                    "https://www.happiestminds.com/"
                    "investors/Annual%20Report/"
                    "2024-2025-Q4/"
                    "HappiestMindsBRSR04072025.pdf"
                )
            }
        ]
    },

    # ========================================================
    # 19. SONATA SOFTWARE
    # ========================================================

    "SONATSOFTW": {
        "company_name": "Sonata Software",
        "aliases": [
            "sonata software",
            "sonata software limited"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": (
                    "https://nsearchives.nseindia.com/"
                    "corporate/"
                    "SONATSOFTW_04072025194537_"
                    "Sefilingbrsr.pdf"
                )
            }
        ]
    },

    # ========================================================
    # 20. HEXAWARE
    # ========================================================

    "HEXAWARE": {
        "company_name": "Hexaware Technologies",
        "aliases": [
            "hexaware",
            "hexaware technologies",
            "hexaware technologies limited"
        ],
        "financial_year": "2024-25",
        "sources": [
            {
                "priority": 1,
                "name": "NSE",
                "url": None
            },
            {
                "priority": 2,
                "name": "BSE",
                "url": None
            },
            {
                "priority": 3,
                "name": "Hexaware Official",
                "url": None
            }
        ]
    },

    # ========================================================
    # 21. DATAMATICS
    # ========================================================

    "DATAMATICS": {
        "company_name": "Datamatics Global Services",
        "aliases": [
            "datamatics",
            "datamatics global services",
            "datamatics global services limited",
            "datamatics global services ltd",
            "datamatics global limited",
            "dgsl",
        ],
        "ticker": "DATAMATICS.NS",
        "financial_year": "2024-25",
        "sources": [
            {"priority": 1, "name": "Datamatics Official Annual Report",
             "url": "https://www.datamatics.com/hubfs/Investors/Financials/2024-25/annual-report24-25.pdf"}
        ],
    },

    # ========================================================
    # 22. MASTEK
    # ========================================================

    "MASTEK": {
        "company_name": "Mastek",
        "aliases": ["mastek", "mastek limited"],
        "ticker": "MASTEK.NS",
        "financial_year": "2024-25",
        "sources": [{"priority": 1, "name": "NSE BRSR", "url": "AUTO"}],
    },

    # ========================================================
    # 23. SONACOMS
    # ========================================================

    "SONACOMS": {
        "company_name": "Sona BLW Precision Forgings",
        "aliases": ["sona blw", "sona blw precision forgings", "sona comstar"],
        "ticker": "SONACOMS.NS",
        "financial_year": "2024-25",
        "sources": [
            {"priority": 1, "name": "Sona Comstar Official BRSR",
             "url": "https://sonacomstar.com/files/documents/sustainability-report-for-fy2024-25-document-ADM1yq.pdf"},
            {"priority": 2, "name": "NSE BRSR", "url": "AUTO"},
        ],
    },

    # ========================================================
    # 24. HGS
    # ========================================================

    "HGS": {
        "company_name": "Hinduja Global Solutions",
        "aliases": ["hgs", "hinduja global solutions", "hinduja global solutions limited"],
        "ticker": "HGS.NS",
        "financial_year": "2024-25",
        "sources": [
            {"priority": 1, "name": "HGS Official BRSR",
             "url": "https://hgs.com/wp-content/uploads/2025/09/BRSR-1.pdf"},
            {"priority": 2, "name": "NSE BRSR", "url": "AUTO"},
        ],
    },

    # ========================================================
    # 25. NIIT
    # ========================================================

    "NIITLTD": {
        "company_name": "NIIT",
        "aliases": ["niit", "niit limited", "niit learning systems"],
        "ticker": "NIITLTD.NS",
        "financial_year": "2024-25",
        "sources": [{"priority": 1, "name": "NSE BRSR", "url": "AUTO"}],
    },

    # ========================================================
    # 26. MPS
    # ========================================================

    "MPSLTD": {
        "company_name": "MPS",
        "aliases": ["mps", "mps limited", "mps ltd"],
        "ticker": "MPSLTD.NS",
        "financial_year": "2024-25",
        "sources": [{"priority": 1, "name": "NSE BRSR", "url": "AUTO"}],
    },

    # ========================================================
    # 27. ETERNAL / ZOMATO
    # ========================================================

    "ZOMATO": {
        "company_name": "Eternal (formerly Zomato)",
        "aliases": ["eternal", "eternal limited", "zomato", "zomato limited"],
        "ticker": "ETERNAL.NS",
        "financial_year": "2024-25",
        "sources": [
            {"priority": 1, "name": "Eternal Official Annual Report",
             "url": "https://b.zmtcdn.com/investor-relations/Eternal_Annual_Report_2024-25.pdf"},
            {"priority": 2, "name": "NSE BRSR", "url": "AUTO"},
        ],
    },

    # ========================================================
    # 28. POLICYBAZAAR
    # ========================================================

    "POLICYBZR": {
        "company_name": "PB Fintech",
        "aliases": ["pb fintech", "policybazaar", "policy bazaar"],
        "ticker": "POLICYBZR.NS",
        "financial_year": "2024-25",
        "sources": [
            {"priority": 1, "name": "PB Fintech Official BRSR",
             "url": "https://www.pbfintech.in/pdf/bussiness-responsibility-and-sustainability-report-fy-24-25.pdf"},
            {"priority": 2, "name": "NSE BRSR", "url": "AUTO"},
        ],
    },

    # ========================================================
    # 29. DELHIVERY
    # ========================================================

    "DELHIVERY": {
        "company_name": "Delhivery",
        "aliases": ["delhivery", "delhivery limited"],
        "ticker": "DELHIVERY.NS",
        "financial_year": "2024-25",
        "sources": [{"priority": 1, "name": "NSE BRSR", "url": "AUTO"}],
    },

    # ========================================================
    # 30. NAZARA
    # ========================================================

    "NAZARA": {
        "company_name": "Nazara Technologies",
        "aliases": ["nazara", "nazara technologies", "nazara technologies limited"],
        "ticker": "NAZARA.NS",
        "financial_year": "2024-25",
        "sources": [
            {"priority": 1, "name": "Nazara Official Annual Report",
             "url": "https://investors.nazara.com/content/pdfs/financials/68bc10bdc953599b2c99e1d8_nazaraannualreport.pdf"},
            {"priority": 2, "name": "NSE BRSR", "url": "AUTO"},
        ],
    },

    # ========================================================
    # 31. MAZAGON DOCK
    # ========================================================

    "MAZDOCK": {
        "company_name": "Mazagon Dock Shipbuilders",
        "aliases": ["mazagon dock", "mazagon dock shipbuilders", "mazdock"],
        "ticker": "MAZDOCK.NS",
        "financial_year": "2024-25",
        "sources": [{"priority": 1, "name": "NSE BRSR", "url": "AUTO"}],
    },

    # ========================================================
    # 32. TITAN
    # ========================================================

    "TITAN": {
        "company_name": "Titan Company",
        "aliases": ["titan", "titan company", "titan company limited"],
        "ticker": "TITAN.NS",
        "financial_year": "2024-25",
        "sources": [
            {"priority": 1, "name": "Titan Official Annual Report",
             "url": "https://www.titancompany.in/sites/default/files/2025-07/Titan%20AR%202024-25%20%281%29%20%281%29.pdf"},
            {"priority": 2, "name": "NSE BRSR", "url": "AUTO"},
        ],
    },
}


_target_file = Path(__file__).resolve().parent.parent / "config" / "companies.csv"
with _target_file.open("r", encoding="utf-8-sig", newline="") as file:
    for row in csv.DictReader(file):
        code = row["company_code"].strip()
        if code in BRSR_MANIFEST:
            continue
        company_name = row["company_name"].strip()
        BRSR_MANIFEST[code] = {
            "company_name": company_name,
            "aliases": [
                company_name.lower(),
                row["search_alias"].strip().lower(),
                code.lower(),
            ],
            "financial_year": "2024-25",
            "ticker": row["ticker"].strip(),
            "sources": [
                {
                    "priority": 1,
                    "name": "NSE BRSR",
                    "url": "AUTO",
                }
            ],
        }

# (the manifest may hold entries for excluded companies; the cohort itself is config/companies.csv)
