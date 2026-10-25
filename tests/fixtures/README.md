Real Bureau of Meteorology pages used as parser test fixtures:

- `IDQ60286_2026-10-22.html`: river height bulletin for the Brisbane, Pine and Caboolture rivers, issued 10:24 am AEST Thursday 22 October 2026.
- `IDQ65389.540111.tbl.shtml`: 7-day table for Kedron Brook at Mitchelton (Osborne Rd), issued 10:38 am AEST the same day.
- `IDQ60286_2026-10-26.html`, `IDQ60285_2026-10-26.html`, `IDQ65325_2026-10-26.html`: the Brisbane/Pine/Caboolture and Gold Coast/Logan-Albert/Tingalpa bulletins and the flood-warning map page, captured from BoM's feed at 7:44 am AEST Monday 26 October 2026 (during flooding in the Lockyer, Bremer and Logan catchments).
- `council_locations.json`: gauge positions from the Brisbane City Council and City of Moreton Bay registers, 23 October 2026.

© Commonwealth of Australia, Bureau of Meteorology. Licensed under Creative Commons Attribution 4.0 Australia. Most river height data is supplied to the Bureau by other agencies.

The website preview (`tools/make_preview.py`) uses the 26 October captures as its labelled sample snapshot. New captures can be made with the *Capture BoM feed files* workflow.
