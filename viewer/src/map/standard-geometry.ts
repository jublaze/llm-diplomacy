export type MapTerrain = 'land' | 'sea' | 'impassable';
export type MapPoint = { x: number; y: number; terrain: MapTerrain };

export const STANDARD_MAP_VIEWBOX = '0 0 610 560';

/**
 * Canonical anchors derived from the labels in public/maps/standard_clean.svg.
 * Coordinates and the SVG deliberately share one 610 x 560 coordinate system.
 */
export const standardGeometry: Record<string, MapPoint> = {
  nwg:{x:220,y:70,terrain:'sea'}, nth:{x:190,y:230,terrain:'sea'}, swi:{x:215,y:378,terrain:'impassable'}, adr:{x:308,y:460,terrain:'sea'}, aeg:{x:392,y:510,terrain:'sea'},
  alb:{x:333,y:460,terrain:'land'}, ank:{x:510,y:455,terrain:'land'}, apu:{x:291,y:470,terrain:'land'}, arm:{x:585,y:467,terrain:'land'}, bal:{x:308,y:260,terrain:'sea'},
  bar:{x:440,y:15,terrain:'sea'}, bel:{x:192,y:321,terrain:'land'}, ber:{x:272,y:292,terrain:'land'}, bla:{x:500,y:418,terrain:'sea'}, boh:{x:283,y:347,terrain:'land'},
  bre:{x:130,y:345,terrain:'land'}, bud:{x:350,y:390,terrain:'land'}, bul:{x:395,y:443,terrain:'land'}, bur:{x:185,y:371,terrain:'land'}, cly:{x:133,y:201,terrain:'land'},
  con:{x:435,y:483,terrain:'land'}, den:{x:250,y:235,terrain:'land'}, eas:{x:455,y:550,terrain:'sea'}, edi:{x:152,y:202,terrain:'land'}, eng:{x:135,y:306,terrain:'sea'},
  fin:{x:375,y:160,terrain:'land'}, gal:{x:355,y:343,terrain:'land'}, gas:{x:130,y:400,terrain:'land'}, gre:{x:352,y:490,terrain:'land'}, lyo:{x:170,y:457,terrain:'sea'},
  bot:{x:328,y:175,terrain:'sea'}, hel:{x:220,y:265,terrain:'sea'}, hol:{x:210,y:290,terrain:'land'}, ion:{x:315,y:520,terrain:'sea'}, iri:{x:95,y:270,terrain:'sea'},
  kie:{x:237,y:285,terrain:'land'}, lvp:{x:138,y:230,terrain:'land'}, lvn:{x:380,y:260,terrain:'land'}, lon:{x:160,y:280,terrain:'land'}, mar:{x:173,y:412,terrain:'land'},
  mao:{x:50,y:355,terrain:'sea'}, mos:{x:460,y:265,terrain:'land'}, mun:{x:235,y:360,terrain:'land'}, nap:{x:293,y:493,terrain:'land'}, nao:{x:65,y:120,terrain:'sea'},
  naf:{x:130,y:536,terrain:'land'}, nwy:{x:250,y:175,terrain:'land'}, par:{x:155,y:358,terrain:'land'}, pic:{x:168,y:326,terrain:'land'}, pie:{x:215,y:408,terrain:'land'},
  por:{x:22,y:440,terrain:'land'}, pru:{x:335,y:283,terrain:'land'}, rom:{x:257,y:452,terrain:'land'}, ruh:{x:215,y:330,terrain:'land'}, rum:{x:410,y:415,terrain:'land'},
  ser:{x:350,y:450,terrain:'land'}, sev:{x:540,y:350,terrain:'land'}, sil:{x:304,y:325,terrain:'land'}, ska:{x:255,y:220,terrain:'sea'}, smy:{x:460,y:510,terrain:'land'},
  spa:{x:85,y:450,terrain:'land'}, stp:{x:460,y:149,terrain:'land'}, swe:{x:300,y:170,terrain:'land'}, syr:{x:570,y:535,terrain:'land'}, tri:{x:305,y:425,terrain:'land'},
  tun:{x:210,y:555,terrain:'land'}, tus:{x:240,y:425,terrain:'land'}, tyr:{x:255,y:380,terrain:'land'}, tys:{x:245,y:495,terrain:'sea'}, ukr:{x:420,y:340,terrain:'land'},
  ven:{x:245,y:407,terrain:'land'}, vie:{x:307,y:370,terrain:'land'}, wal:{x:130,y:275,terrain:'land'}, war:{x:355,y:304,terrain:'land'}, wes:{x:160,y:491,terrain:'sea'},
  yor:{x:155,y:254,terrain:'land'}
};

const coastGeometry: Record<string, MapPoint> = {
  bul_ec:{x:409,y:439,terrain:'land'}, bul_sc:{x:393,y:454,terrain:'land'},
  spa_nc:{x:79,y:441,terrain:'land'}, spa_sc:{x:91,y:461,terrain:'land'},
  stp_nc:{x:462,y:137,terrain:'land'}, stp_sc:{x:470,y:160,terrain:'land'}
};

const aliases: Record<string, string> = { gol:'lyo', liv:'lvn' };

export const supplyCenters = new Set([
  'ank','bel','ber','bre','bud','bul','con','den','edi','gre','hol','kie','lon','lvp','mar','mos','mun','nap','nwy','par','por','rom','rum','ser','sev','smy','spa','stp','swe','tri','tun','ven','vie','war'
]);

export function canonicalProvinceId(location: string): string | undefined {
  const normalized = location?.toLowerCase();
  if (coastGeometry[normalized]) return normalized.slice(0, 3);
  if (standardGeometry[normalized]) return normalized;
  return aliases[normalized];
}

/** Returns only explicit standard-board geometry; unknown locations stay textual-only. */
export function pointForProvince(location: string): MapPoint | undefined {
  const normalized = location?.toLowerCase();
  return coastGeometry[normalized] ?? standardGeometry[aliases[normalized] ?? normalized];
}

export function isSupplyCenter(location: string): boolean {
  const canonical = canonicalProvinceId(location);
  return Boolean(canonical && supplyCenters.has(canonical));
}
