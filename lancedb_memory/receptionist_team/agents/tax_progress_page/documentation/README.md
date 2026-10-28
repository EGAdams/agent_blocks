# tax_progress_page

"Toyota, build us the current tax progress" routes here. This specialist runs
`/home/adamsl/rol_finances/adws/adw_tax_progress_page.py` and relays the one
line it writes.

- The page is `Barnes_2025_Tax_Progress_<time>.html`, newest on top of
  ROL Finance -> Reports -> ADW Generated. The dashboard serves that folder
  from disk, so nothing needs restarting.
- Read-only: no AI, no database or answer writes. Same checks as `just tax-status`.
- Every build is a run in `just obs`.
