# Racing Track convergence inventory

This inventory records the operator-supplied structural export and source-mapping state reviewed on 17 November 2026. It is a convergence plan, not seed data: a Track's Region is On Track's operational rostering Region and must not be inferred from geography.

## Confirmed canonical Tracks and mappings

| Source identity | Canonical Track | State | Note |
|---|---|---|---|
| Avondale | Avondale | Confirmed | Existing canonical Track |
| Cambridge Synthetic | Cambridge Synthetic | Confirmed | Keep distinct from Cambridge harness/other Cambridge venue identities |
| Pukekohe Park | Pukekohe | Confirmed alias | Source and canonical labels deliberately differ |
| Ruakaka | Ruakaka | Confirmed | Existing canonical Track |
| Tauranga | Tauranga | Confirmed | Existing canonical Track |
| Te Aroha | Te Aroha | Confirmed | Existing canonical Track |
| Te Rapa | Te Rapa | Confirmed | Existing canonical Track |
| Ellerslie | Ellerslie | Existing mapping | Existing canonical Track |
| Taupo | Taupo | Existing mapping | Existing canonical Track |
| Arawa Park | Arawa Park | Existing mapping | Preserve the canonical physical-course label |

All currently active canonical Tracks in the supplied export were assigned to Northern. Consequently, a valid imported meeting at any other physical course remains unresolved until the canonical catalogue is completed and an operator chooses its operational Region.

## Missing canonical physical Tracks

These Love Racing venue identities appear to represent physical courses absent from the supplied catalogue. Create each Track once through the existing provider-neutral structural master-data workflow, then confirm the source mapping. The proposed Region is deliberately left unset.

| Source identity / proposed canonical label | Operational Region | Review note |
|---|---|---|
| Hastings | Operator decision required | Confirm preferred canonical course label |
| New Plymouth Raceway | Operator decision required | Confirm whether the canonical label retains “Raceway” |
| Wanganui | Operator decision required | Confirm the operational canonical spelling; do not create both Wanganui and Whanganui |
| Trentham | Operator decision required | No geographic Region inference |
| Otaki / Ōtaki | Operator decision required | Confirm macron policy and map both spellings to one Track if both are observed |
| Riccarton Park | Operator decision required | Physical turf course |
| Riccarton Park Synthetic | Operator decision required | Keep distinct from Riccarton Park |
| Ashburton | Operator decision required | Confirm source aliases such as “Ashburton Raceway” against one physical Track |
| Phar Lap Raceway | Operator decision required | Confirm canonical label |
| Wingatui | Operator decision required | No geographic Region inference |
| Ascot Park | Operator decision required | Confirm that racing-source evidence refers to the intended physical course |
| Cromwell | Operator decision required | Confirm canonical label |
| Gore | Operator decision required | Confirm canonical label |
| Tauherenikau | Operator decision required | Confirm canonical label |

Foxton also appeared in the bounded 17–30 November official Love Racing calendar check and was not in the operator-supplied missing list. Its current catalogue/mapping state needs operator confirmation before creation or mapping.

## Identities that remain review items

- HRNZ Racing Dates commonly identifies a club, not a physical venue. Club-only values must stay unresolved unless the same bounded official evidence explicitly names the venue.
- Relocated harness meetings, `Club @ Venue` programme labels, and tentative meetings need event-specific evidence; they must not create a permanent club-to-Track alias automatically.
- Cambridge Synthetic, other Cambridge racing identities, Riccarton Park, and Riccarton Park Synthetic are deliberately distinct until the operator confirms the exact physical-course relationship.
- Spelling, macron, `Park`/`Raceway`, and club-name variations are aliases for review, not reasons to create duplicate Tracks.

## Recommended convergence workflow

1. The operator assigns each missing physical Track to an operational Region and confirms its canonical label.
2. Import the resulting Regions/Tracks with the existing structural master-data Preview/Apply workflow; do not add another mapping administration page.
3. Confirm each observed source identity to the existing Track in Online Sources.
4. Let confirmation replay stored unmatched observations. Repeated provider refreshes remain idempotent and same-date Race/Trial rows remain separate.
5. Review the resulting future events before adopting any into a private Builder draft. Source evidence never publishes a roster.

Addresses, map references and automatic-map support are separate decisions and are not invented by this inventory.
