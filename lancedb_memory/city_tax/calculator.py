"""From sourced figures to a city return, and the checks on it.

`GrandRapidsResidentCalculator` (ICityTaxCalculator) applies the year's
`CityTaxRules` to the federal figures:

* church pay goes on line 4 (business income), the way the 2024 return and
  the family's plan report it. If the CPA/EA says W-2, it moves to line 1;
  the taxable amount does not change, because the city taxes both.
* the housing allowance is left out, as on the federal return and the 2024
  city return (the city follows the federal figures; flagged for review);
* Social Security, the self-employed health insurance deduction and one-half
  of SE tax never reach the city return -- the instructions exclude or
  disallow each, and the form has no line for them;
* every amount is rounded to whole dollars, half up, as the form says.

A figure that is MISSING stays None on its line and in every total that
depends on it: no total is computed from a blank.

`verify` recomputes everything independently from the lines and the rules.
`CityFormFieldMapper` (ITaxFieldMapper) names the 2025 fillable PDF's fields.
`reconcile` builds the federal-to-city and 2024-to-2025 tables.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .models import ( CheckResult, CityReturnDraft, CityTaxRules, Figure, FigureStatus, Residency, ReturnLine,
                      ReturnStatus, SourceKind, SourceRef )
from .store import whole_dollars

ZERO = Decimal( "0" )


def _get( figures: list[ Figure ], key: str ) -> Figure | None:
    return next(( f for f in figures if f.key == key ), None )


def _sum( values: list[ Decimal | None ]) -> Decimal | None:
    return None if any( v is None for v in values ) else sum( values, ZERO )


class GrandRapidsResidentCalculator:
    def calculate( self, figures: list[ Figure ], rules: CityTaxRules, residency: Residency,
                   exemptions: int, payments: Figure ) -> CityReturnDraft:
        if residency is not Residency.RESIDENT:
            raise ValueError( "Only the resident return is prepared; residency is not settled as resident." )
        questions: list[ str ] = []
        assumptions: list[ str ] = []
        pay = [ _get( figures, "church_pay_robert" ), _get( figures, "church_pay_rosemary" )]
        interest = _get( figures, "taxable_interest" )
        rate_ref = rules.sources[ 0 ] if rules.sources else SourceRef( kind=SourceKind.OFFICIAL_FORM, where="?" )
        computed = SourceRef( kind=SourceKind.COMPUTED, where="city_tax.calculator" )

        business = _sum([ whole_dollars( f.amount ) if f and f.amount is not None else None for f in pay ])
        interest_amount = whole_dollars( interest.amount ) if interest and interest.amount is not None else None
        lines: list[ ReturnLine ] = [
            ReturnLine( line="2", label="Taxable interest", column_a=interest_amount,
                        column_b=ZERO if interest_amount is not None else None, column_c=interest_amount,
                        explanation="Bank interest is taxable to residents (not U.S. obligations).",
                        sources=interest.sources if interest else []),
            ReturnLine( line="4", label="Business income (federal Schedule C)", column_a=business,
                        column_b=ZERO if business is not None else None, column_c=business,
                        explanation=( "Robert's and Rosemary's church pay, each rounded as it would be on its "
                                      "own Schedule C. Housing allowance is not included: it is excluded from "
                                      "federal income tax and the city follows the federal figure." ),
                        sources=[ s for f in pay if f for s in f.sources ]),
        ]
        total = _sum([ interest_amount, business ])
        deductions = ZERO
        after = None if total is None else total - deductions
        exemption_total = rules.exemption_amount * exemptions
        subject = None if after is None else max( after - exemption_total, ZERO )
        rate = rules.resident_rate
        tax = None if subject is None else whole_dollars( subject * rate )
        paid = whole_dollars( payments.amount ) if payments.amount is not None else None
        lines += [
            ReturnLine( line="12", label="Total additions (lines 2-11)", column_a=total, column_c=total,
                        sources=[ computed ]),
            ReturnLine( line="13", label="Total income", column_a=total, column_c=total, sources=[ computed ]),
            ReturnLine( line="14", label="Total deductions", column_c=deductions,
                        explanation="No IRA, SEP or other allowable deduction. The self-employed health insurance "
                                    "deduction and one-half of SE tax are not allowed by the city.",
                        sources=[ rate_ref ]),
            ReturnLine( line="15", label="Income after deductions", column_c=after, sources=[ computed ]),
            ReturnLine( line="16a", label="Number of exemptions", column_c=Decimal( exemptions ),
                        explanation="Each spouse: one regular, plus one for 65 or over.", sources=[ rate_ref ]),
            ReturnLine( line="16b", label=f"Exemptions x ${ rules.exemption_amount:,.0f}", column_c=exemption_total,
                        sources=[ rate_ref ]),
            ReturnLine( line="17", label="Income subject to tax", column_c=subject, sources=[ computed ]),
            ReturnLine( line="18b", label=f"Tax at { rate * 100:.2f}% (resident)", column_c=tax,
                        explanation="Rounded to whole dollars.", sources=[ rate_ref ]),
            ReturnLine( line="19a", label="Grand Rapids tax withheld", column_c=ZERO,
                        explanation="River of Life issues no W-2, so nothing was withheld.",
                        sources=[ SourceRef( kind=SourceKind.WORKPAPER, where="tax plan", line="W-2 / 1099: neither" )]),
            ReturnLine( line="19b", label="Estimated / extension payments and credit forward", column_c=paid,
                        explanation=payments.note, sources=payments.sources ),
            ReturnLine( line="19d", label="Total payments and credits", column_c=paid, sources=[ computed ]),
        ]
        if tax is not None and paid is not None:
            owed, over = max( tax - paid, ZERO ), max( paid - tax, ZERO )
            lines += [ ReturnLine( line="21", label="Amount owed", column_c=owed, sources=[ computed ]),
                       ReturnLine( line="22", label="Overpayment", column_c=over, sources=[ computed ])]
        else:
            lines += [ ReturnLine( line="21", label="Amount owed", explanation="Waits on line 19b." ),
                       ReturnLine( line="22", label="Overpayment", explanation="Waits on line 19b." )]
        lines.append( ReturnLine( line="20", label="Interest and penalty",
                                  explanation=( f"The return was due { rules.due_date:%B %-d, %Y}. Late tax is charged "
                                                "1% a month penalty (up to 25%) plus interest; Liz leaves this line "
                                                "for the city to compute rather than guess it." ),
                                  sources=[ rate_ref ]))

        if payments.amount is None:
            questions.append( "Were any 2025 Grand Rapids estimated payments, extension payments or a credit "
                              "forward made? (Line 19b is blank until a document or an answer settles it.)" )
        if any( f is None or f.status is FigureStatus.PROVISIONAL for f in pay ):
            questions.append( "Church pay is provisional until the CPA/EA settles employee vs self-employed and "
                              "the van values. The city's taxable amount is the same either way, but a W-2 "
                              "moves it from line 4 to line 1." )
        assumptions.append( "Housing allowance actually paid is excluded, as on the federal return and the 2024 "
                            "city return. Confirm with the city if the CPA/EA changes the federal treatment." )
        assumptions.append( "Health insurance and medical bills paid by the church are taxed here because they are "
                            "inside church pay. If the CPA/EA calls them employer-provided coverage of an "
                            "employee, they may be excludable -- ask." )
        if any( v is None for v in ( total, payments.amount )):
            status = ReturnStatus.NEEDS_DOCUMENTS
        elif questions:
            status = ReturnStatus.NEEDS_REVIEW
        else:
            status = ReturnStatus.READY_FOR_APPROVAL
        return CityReturnDraft( tax_year=rules.tax_year, residency=residency, filing_status="Married filing jointly",
                                exemptions=exemptions, lines=lines, status=status, open_questions=questions,
                                assumptions=assumptions, prepared_at=datetime.now().astimezone())


def verify( draft: CityReturnDraft, rules: CityTaxRules ) -> list[ CheckResult ]:
    """Independent re-arithmetic over the draft's own lines."""
    c = lambda n: ( draft.line( n ).column_c if draft.line( n ) else None )
    checks: list[ CheckResult ] = []

    def check( name: str, ok: bool, detail: str = "" ) -> None:
        checks.append( CheckResult( name=name, passed=bool( ok ), detail=detail ))

    parts = [ c( n ) for n in ( "2", "4" )]
    if None not in parts:
        check( "line 13 = line 2 + line 4", c( "13" ) == sum( parts, ZERO ), f"{ c( '13' )} vs { sum( parts, ZERO )}" )
        check( "line 15 = line 13 - line 14", c( "15" ) == c( "13" ) - c( "14" ))
        check( "line 16b = 16a x exemption amount", c( "16b" ) == c( "16a" ) * rules.exemption_amount )
        check( "line 17 = line 15 - line 16b", c( "17" ) == max( c( "15" ) - c( "16b" ), ZERO ))
        check( "line 18b = line 17 x resident rate, whole dollars",
               c( "18b" ) == whole_dollars( c( "17" ) * rules.resident_rate ),
               f"{ c( '17' )} x { rules.resident_rate } = { c( '17' ) * rules.resident_rate }" )
        check( "every amount is whole dollars", all( v == v.to_integral_value() for l in draft.lines
                                                       for v in ( l.column_a, l.column_b, l.column_c ) if v is not None ))
    else:
        check( "income lines present", False, "line 2 or line 4 is blank" )
    if c( "19d" ) is not None and c( "18b" ) is not None:
        check( "line 21 / 22 = line 18b vs 19d", ( c( "21" ) or ZERO ) - ( c( "22" ) or ZERO ) == c( "18b" ) - c( "19d" ))
    else:
        check( "payments known", False, "line 19b is blank -- owed or refund can't be computed yet" )
    if c( "19b" ) is None:
        check( "a blank line 19b is never treated as zero", all( c( n ) is None for n in ( "19d", "21", "22" )),
               "19d, 21 and 22 stay blank until payments are known" )
    return checks


PARTS = ( "salary", "se_tax_paid_back", "offerings", "wedding_fee", "van_personal_use", "priority_health",
          "medical_paid", "trustage" )


def parts_check( figures: list[ Figure ]) -> list[ CheckResult ]:
    """Each pastor's church pay equals the sum of its listed parts, to the cent."""
    by = { f.key: f.amount for f in figures if f.amount is not None }
    out: list[ CheckResult ] = []
    for who in ( "robert", "rosemary" ):
        total = by.get( f"church_pay_{ who }" )
        parts = sum(( by.get( f"{ p }_{ who }", ZERO ) for p in PARTS ), ZERO )
        out.append( CheckResult( name=f"church pay ({ who.title()}) = sum of its parts",
                                 passed=total is not None and total == parts,
                                 detail=f"total { total } vs parts { parts }" ))
    return out


def books_check( figures: list[ Figure ], rows: list[ dict ]) -> list[ CheckResult ]:
    """Church-paid premiums in pay match the church's own books (read-only evidence)."""
    by = { f.key: f.amount for f in figures if f.amount is not None }
    books = { r[ "category" ]: Decimal( r[ "total" ]) for r in rows }
    pairs = (( "priority_health_robert", "RJ — Priority Health" ), ( "priority_health_rosemary", "RM — Priority Health" ),
             ( "trustage_robert", "TruStage Life Insurance" ))
    out: list[ CheckResult ] = []
    for key, prefix in pairs:
        booked = next(( v for name, v in books.items() if name.startswith( prefix )), None )
        if key in by and booked is not None:
            out.append( CheckResult( name=f"{ key.replace( '_', ' ' )} matches the church's books",
                                     passed=by[ key ] == booked, detail=f"plan { by[ key ]} vs books { booked }" ))
    return out


def _money( value: Decimal | None ) -> str:
    return "" if value is None else f"{ value:,.0f}"


class CityFormFieldMapper:
    """ITaxFieldMapper for the 2025 GR-1040R fillable PDF (field names read off the PDF itself).

    Identity, SSN, address and bank fields are deliberately not filled: a
    person adds them when signing. The draft carries figures and checkboxes only.
    """

    LINE_FIELDS = { "2": "t2", "4": "t4", "12": "t12", "13": "t13" }
    SINGLE = { "14": "t14", "15": "t15", "16a": "t16a", "16b": "t16b", "17": "t17a", "18b": "t18b",
               "19a": "t19a", "19b": "t19b", "19d": "t19d", "21": "t21", "22": "t22" }

    def fields( self, draft: CityReturnDraft ) -> dict[ str, str ]:
        values: dict[ str, str ] = {}
        for line, stem in self.LINE_FIELDS.items():
            row = draft.line( line )
            if row is None:
                continue
            for column, suffix in (( row.column_a, "a" ), ( row.column_b, "b" ), ( row.column_c, "c" )):
                if column is not None and not ( suffix == "b" and column == ZERO ):
                    values[ f"{ stem }{ suffix }" ] = _money( column )
        for line, name in self.SINGLE.items():
            row = draft.line( line )
            if row is not None and row.column_c is not None:
                values[ name ] = _money( row.column_c ) if line != "16a" else f"{ row.column_c:.0f}"
            else:
                values[ name ] = ""          # unknown stays visibly blank -- the form's own scripts print 0
        # left for the city (interest/penalty) or for EG (donations, credit forward): blank, never 0
        for name in ( "t20a", "t20b", "t20c", "t23a", "t23b", "t23c", "t23d", "t24" ):
            values[ name ] = ""
        over = draft.line( "22" )
        values[ "t25" ] = _money( over.column_c ) if over is not None and over.column_c else ""
        values[ "1 d Enter the number of boxes checked on lines 1 a and 1 b" ] = str( draft.exemptions )
        values[ "1f Total exemptions Add lines 1 d and 1 e enter here and also on page 1 line 16a" ] = str( draft.exemptions )
        values[ "e1a" ] = "2" if draft.exemptions >= 2 else "1"
        values[ "e1b" ] = str( max( draft.exemptions - int( values[ "e1a" ]), 0 ))
        # checkboxes: married filing jointly; regular + 65 or over for each spouse
        values[ "Check Box IJS" ] = "/J"
        for box in ( "Check Box6", "Check Box7", "Check Box9", "Check Box10" ):
            values[ box ] = "/1"
        return values


def reconcile( draft: CityReturnDraft, federal: list[ Figure ], last_year: list[ Figure ]) -> dict:
    """Two tables: federal -> city adjustments, and 2024 -> 2025 by line."""
    f = { x.key: x for x in federal }
    amount = lambda key: f[ key ].amount if key in f else None
    city_total = draft.line( "13" ).column_c if draft.line( "13" ) else None
    federal_rows = []
    for key, label, treatment in (
            ( "church_pay_robert", "Church pay -- Robert", "Taxed (line 4)" ),
            ( "church_pay_rosemary", "Church pay -- Rosemary", "Taxed (line 4)" ),
            ( "taxable_interest", "Bank interest", "Taxed (line 2)" ),
            ( "housing_allowance_paid", "Housing allowance actually paid", "Not on either income tax return" ),
            ( "social_security_robert", "Social Security -- Robert", "City does not tax Social Security" ),
            ( "social_security_rosemary", "Social Security -- Rosemary", "City does not tax Social Security" ),
            ( "se_health_insurance_robert", "SE health insurance deduction -- Robert",
              "Federal adjustment; city does not allow it" ),
            ( "se_health_insurance_rosemary", "SE health insurance deduction -- Rosemary",
              "Federal adjustment; city does not allow it" )):
        federal_rows.append({ "item": label, "federal": amount( key ), "treatment": treatment,
                              "status": f[ key ].status.value if key in f else "missing" })
    ly = { x.key: x.amount for x in last_year }
    pairs = [( "Total income (line 13)", "gr2024_total", "13" ), ( "Exemptions (line 16b)", "gr2024_exemption_amount", "16b" ),
             ( "Income subject to tax (line 17)", "gr2024_taxable", "17" ), ( "City tax (line 18b)", "gr2024_tax", "18b" ),
             ( "Payments (line 19d)", "gr2024_payments", "19d" ), ( "Refund (line 25)", "gr2024_refund", "22" ),
             ( "Amount owed (line 21)", None, "21" )]
    year_rows = []
    for label, key, line in pairs:
        before = ly.get( key ) if key else ZERO
        row = draft.line( line )
        after = row.column_c if row else None
        change = None if before is None or after is None else after - before
        year_rows.append({ "item": label, "2024": before, "2025": after, "change": change })
    why = ""
    if ly.get( "gr2024_total" ) is not None and city_total is not None:
        why = ( f"Total income moved from ${ ly[ 'gr2024_total' ]:,.0f} to ${ city_total:,.0f}. The city's rate and "
                f"exemption amount are the same in both years' official instructions, so the difference comes from "
                f"income, not from a change in the rules. 2024's figure was the two Schedule C profits; 2025's is "
                f"church pay as the family's plan totals it -- compare its parts with the 2024 pay records if the "
                f"change looks too large or too small." )
    return { "federal_to_city": federal_rows, "city_total": city_total, "year_over_year": year_rows, "why": why }
