`airport_regions.json` contains IATA airport-to-continent mappings derived from
the public-domain OurAirports dataset, distributed by
https://github.com/datasets/airport-codes (retrieved 2026-10-28 UTC).

Source and terms: https://ourairports.com/data/

The monitor uses this mapping to identify flight segments entering or leaving
the Americas. Every such segment actually flown must have Google Flights cabin
code 3 (business). Unknown airports fail validation. Domestic feeder segments
may use other cabins. This is a conservative itinerary filter, not a worldwide
ocean-boundary geometry model.

`china_airports.json` is a snapshot of scheduled-service fixed-wing airports in
the OurAirports regions CN, HK, MO and TW. It includes HKG and excludes heliports,
closed airports, and airfields without scheduled-service markings. Refresh it
manually using `python scripts/update_china_airports.py`.

The nationwide scanner searches every catalogued airport in groups of at most
seven and records the actual airport codes returned by Google. A batch search
is a Google comparison query, not a guarantee that Google lists every possible
itinerary from every airport. Unsupported airports or unavailable fares can
produce empty results; query counts and failures appear in the private summary.
