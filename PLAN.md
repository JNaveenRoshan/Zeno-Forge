# Plan v2: Zeno Car Plants, Source-to-Pay World (rebuild)

## What changed since v1 (your feedback)
- Read the full PDF (all 21 pages). Its pictures are AI-painted concept renders ("your build will look different"). The look we're aiming for is that **miniature diorama** style: warm light, soft shadows, slight tilt-shift blur, lots of small detail.
- v1 looked like Lego because everything was plain boxes. v2 swaps in real 3D models and much better rendering.

## Research: 3D models (free, licence-checked)
| Use | Source | Licence | Look |
|---|---|---|---|
| Trucks, vans, forklifts, cars in car parks | Kenney **Car Kit** (45 files, glTF) | CC0, no credit needed | Clean, slightly stylised |
| Conveyors, rollers, crates, pallets | Kenney **Conveyor Kit** + **Factory Kit** (glTF) | CC0 | Clean, slightly stylised |
| Finished cars (yard, test track) | Quaternius **Realistic Car Pack** (2.6 MB, OBJ/FBX) | CC0 | More realistic proportions |
| Robot arms, chassis, body shell, battery, engine | Built in code, with far more detail than v1 | n/a | Matches the models above |

Honest limit: **free** models are all a bit stylised. Truly photoreal robot arms, forklifts or trucks are paid models (around $20–60 each on CGTrader). We can swap those in later.

Download note: Kenney and itch.io don't give direct download links. Either you download the 4 zips into `renault/assets/raw/` (links below), or I try fetching them, with your OK on each one.
- https://kenney.nl/assets/car-kit
- https://kenney.nl/assets/conveyor-kit
- https://kenney.nl/assets/factory-kit
- https://quaternius.itch.io/lowpoly-cars (Realistic Car Pack)

## Rendering upgrade (closer to the PDF)
Tone mapping, environment reflections (car paint and glass shine), ambient occlusion (soft contact shadows), bloom for lights at night, a light tilt-shift blur when zoomed out, and better trees and ground textures.

## Site layout
- **Two separate plant campuses**, about 150 m apart, with landscaping, car parks and a boulevard between them: EV campus on the left, petrol campus on the right.
- Each campus has its own inbound yard, gatehouse, dock doors and warehouse.
- **Supplier Hub** building in the middle (glass pavilion). Click it to see every supplier: parts supplied, which plant, spend, on-time %, risk, and an **ESG score** (E/S/G breakdown, CO₂ per part, certifications). All made-up data.
- **Rail freight** spur, clearly labelled "Rail freight: battery packs & steel coil", ending at the EV campus rail dock.
- Shared test track and finished-car yard, then cars leave on car-carrier trucks.

## Inbound (smooth, no flying crates)
Truck reverses into the dock → dock leveller drops → pallets roll off on a **dock conveyor** → forklift or robot cart takes them to the racks → goods receipt posted (S2P step).

## Inside each plant: one long conveyor, separate zones
1. **Parts supermarket** (start of line): every component on labelled racks, *part · supplier · € unit*, colour-coded by supplier.
2. **Chassis**: skateboard (EV) or ladder frame (petrol) placed on the conveyor.
3. **Suspension & wheels**: robots fit corners and wheels.
4. **Battery (EV) / engine + gearbox (petrol)**: separate zone per plant; hoist lowers the pack or engine in.
5. **Body drop**: painted body lowered onto the chassis.
6. **Interior & electronics**: seats, dashboard, harness.
7. **Glass & doors**.
8. **Quality test**: light tunnel, then drive off to the test track.

Each zone has its parts at the line side with the supplier name, and a "+€" tag as they're fitted. The car's cost builds up exactly as in v1.

## Dashboard
Keep v1's panels (KPIs, S2P tracker, PO/invoice/supplier/cost tables, exception resolve), plus a Supplier Hub view with ESG.

## Build steps
1. Get models (your download or my fetch).
2. Convert/optimise models into `assets/` (I'll write a small script).
3. New layout + rendering upgrade.
4. New conveyor zones + parts supermarket.
5. Docks with conveyors + forklifts.
6. Supplier Hub + ESG panel.
7. Check in browser (desktop + mobile).
