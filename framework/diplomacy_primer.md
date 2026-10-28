# Diplomacy Rules Reference

`rules_reference_version: diplomacy_rules_reference_v5_convoyed_army_support_public_orders`

This reference explains the rules implemented by this project. The authoritative current board state and engine-generated legal-order context override this prose whenever they are more specific.

## Objective and Board

The standard board has seven powers. A standard solo victory is control of 18 supply centers. This experiment runner normally completes at its configured year or budget limit rather than automatically ending when a power reaches 18; its result records the current center totals and leaders.

Provinces are land, coastal land, or sea. Armies occupy land or coastal land, never sea. Fleets occupy sea or coastal provinces, never inland land. Only one active unit may occupy a province. A supply center is owned by a power, while a unit merely occupies a province; these are separate facts until Fall ownership is applied. A home center is a designated original build location, not a permanent ownership guarantee. Some coastal provinces have multiple named coasts. A fleet's coast is part of its position, so fleet movement, support, and builds must use the coast supplied by the engine where one is required.

## Year, Phases, and Authority

Each year has Spring movement, a Spring retreat only when units were dislodged, Fall movement, a Fall retreat only when units were dislodged, and Winter adjustment. Movement and adjustment orders are secret and resolve simultaneously. Retreats are also simultaneous among dislodged units.

The platform may run a negotiation window before movement or adjustment. That window is communication only, not a board-rule phase; it does not make agreements binding and does not reveal secret orders. The engine's legal actions, not this reference, determine exact adjacency, coast, unit, and phase legality.

## Movement and Adjudication

In a movement phase, a unit may hold or attempt one legal move/attack. A normal move is one legal adjacent step for that unit type and coast. Equal-strength attacks on the same destination bounce: neither enters and the destination is a standoff. More strength defeats less strength. A power cannot dislodge one of its own units.

Opposing direct moves between two provinces are resolved as a head-to-head strength contest. The stronger move may succeed; otherwise neither crosses. A reciprocal movement order is therefore not an automatic exchange. A convoyed army is resolved through the convoy rules below. Exact outcomes, including failures caused by occupied provinces or other simultaneous moves, are determined only by adjudication.

## Support Orders

A unit may support another unit's legal hold or legal move when the board geometry permits the supporting unit to affect the supported province. Support adds strength to the supported hold or move during adjudication, subject to ordinary legality and support-cut rules. The supported unit may belong to the same power or a different power. Because orders are simultaneous and agreements are non-binding, a promise of support does not guarantee that a support order was submitted. Support can change whether a move succeeds, a position holds, or an attack fails; it is an ordinary order type, not an ally-only mechanic. Use the authoritative legal-order data for your current board to determine the exact support orders available.

Support-to-hold names an occupied unit's province; it applies only if that unit holds. Support-to-move names the moving unit's origin and destination; it applies only if that matching move is active. The supporter must be able to move to the supported destination under the current map and unit rules. A support order is the supporter's action for the phase.

A support order is legal only when the supporting unit could itself legally move into the supported destination. For support-to-hold, that destination is the unit’s current province. For support-to-move, it is the moving unit’s destination. Armies may support only into adjacent land provinces; fleets may support only into adjacent sea or coastal provinces. A fleet cannot support into an inland province, even if it is adjacent to the moving unit’s origin. For example, `F Alb S A Bul–Ser` is illegal because Serbia is inland and a fleet in Albania cannot enter it.

When supporting an army that moves by convoy, name the convoyed army’s original province and destination—not a convoying fleet’s province. For example, to support `A Yor–Bel` via `F Nth`, use `A Hol S A Yor–Bel`; `A Hol S F Nth–Bel` supports a different move and does not aid the convoy.

An attack on a supporting unit normally cuts its support, even if the attack itself does not succeed. In this engine, an attack from the supported unit's province does not cut support-to-hold, and an attack from the supported destination does not cut support-to-move. A dislodged supporter cannot provide support. The engine resolves all remaining support-cut and same-power-displacement cases from the submitted orders and board state.

### Cross-Power Support and Negotiation

Valid support strength combines across all supporting units, regardless of which power owns them. A power may support another power's legal hold or move; the supporting unit uses its action for that phase and normally remains in place. During negotiation, powers may request, offer, or make a conditional proposal for support of a specific legal hold or move. These proposals are non-binding communication: support has effect only when matching legal orders are submitted and survive ordinary adjudication and support-cut rules. Discussing support may create a tactical opportunity while also revealing intentions, dependencies, or vulnerabilities. Evaluate that strategic and information tradeoff yourself. Use the authoritative legal-order context for the exact currently legal support actions, origins, and destinations.

## Convoys

Only an army can be convoyed. It submits a move with `via_convoy: true`; fleets in convoy-passable sea provinces submit matching convoy orders. The army's origin and destination must be coastal, and the ordered fleets must create a continuous fleet route between them. Intermediate fleets are valid convoy participants even when they touch neither army endpoint. A convoy is possible only if the required fleets are not dislodged during adjudication. When more than one submitted route remains, one surviving continuous route is enough.

The legal-order context lists engine-derived individual convoy actions and current public-board route options. A listed action still needs the matching army move and all required fleet convoy orders to produce a route. The route options describe board geometry, not a prediction that another power will issue its matching order. Convoyed moves participate in ordinary movement and support adjudication.

## Retreats

A unit dislodged in movement must retreat in the following retreat phase or be destroyed. It may retreat only to an otherwise legal adjacent destination that is unoccupied, was not a standoff province, and is not the attacker's origin. Retreats cannot use a convoy. If two units retreat to the same destination, neither survives there. If no legal retreat exists, or no valid retreat is submitted, the unit is disbanded.

## Supply Centers and Winter Adjustment

After Fall movement and any Fall retreats, a unit occupying a supply center makes that power its owner. Spring occupation does not change ownership. A power's number of owned centers sets its permitted active-unit count for Winter adjustment.

If owned centers exceed active units, a power may build up to the difference. A build must be in an owned, vacant home center and must satisfy the chosen unit's terrain and coast requirements. If active units exceed owned centers, the power must disband the required number. This engine deterministically selects required missing disbands, using distance from owned home centers and stable tie-breaking, so submit the engine-listed legal disbands when possible. The adjustment catalogue is authoritative for exact available builds and removals.

## Diplomacy and Evidence

Communication is permitted only in the platform's negotiation windows. Agreements, threats, information, and support commitments are non-binding. Private messages are not public board facts. Orders are secret before adjudication. After adjudication, the final orders that entered adjudication, the adjudicated resolution, and the public board state are authoritative evidence. Do not infer a legal action, a hidden order, or an outcome from this reference when the supplied engine context says otherwise.
