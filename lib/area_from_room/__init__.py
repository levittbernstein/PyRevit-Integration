# -*- coding: utf-8 -*-
"""Area Plan From Rooms — shared library.

Converts a room-bearing floor plan into an Area Plan whose areas mirror the
rooms: boundaries, names/numbers, shared-parameter data, view settings,
annotation, colour scheme, view-template look, key schedules and tags.

Modules
-------
report       tiny result collector for the run summary
params       copy shared/parameter values room -> area
boundaries   room boundary loops -> de-duplicated AreaBoundaryLines
areas        place Areas per room + copy their data
viewsettings crop / scope box / view range / scale / detail level / phase
annotations  copy view-specific detail lines / text / groups
tagging      place area tags where the room was tagged
colorscheme  best-effort replicate the room colour scheme as an area scheme
viewtemplate best-effort reproduce the floor plan's graphic look
keyschedule  best-effort replicate room key schedules for areas

The three "best-effort" modules are isolated and fully wrapped so a failure in
any of them is reported but never aborts the core conversion.
"""
