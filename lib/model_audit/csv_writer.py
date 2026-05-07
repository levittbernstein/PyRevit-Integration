# -*- coding: utf-8 -*-
"""Write audit results to a UTF-8 CSV file (BOM included for Excel compatibility)."""

import csv

HEADERS = [
    'File Name',
    'File Size (MB)',
    'Revit Version',
    'Total Warnings',
    '3D Elements',
    '2D Elements',
    'Model Groups',
    'Detail Groups',
    'Custom Object Styles',
    'CAD Imports',
    'CAD Links',
    'Image Imports',
    'Image Links',
    'PDF Imports',
    'PDF Links',
    'Revit Links',
    'Sheets',
    'Views',
    'Views Not on Sheets',
    'Views with "Copy" in Name',
    'Walls with Edited Profile',
    'In-Place Families',
    'Filled Regions',
    'Families Not Prefixed "LB-"',
    'Reference Planes',
    'Design Options',
]


def write_csv(rows, output_path):
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
