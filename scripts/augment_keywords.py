#!/usr/bin/env python3
"""
augment_keywords.py — add everyday phrasings to violation keyword lists
======================================================================
The resolver is keyword-driven, and the graph's keywords were written in
"legal" English ("Jumping a red traffic light"). Real users say "jumped a red
signal", "three of us on one bike", "black film on my windows", "had been
drinking", "bina helmet". Conversation evals showed these falling through to
a generic topic menu.

This script merges curated synonyms (English, common Indian-English and
Hinglish) into each violation's `keywords` list in the compiled graph. The
server (graph_engine) and the on-device engine (via build_offline_bundle.py)
both build their keyword index from these lists, so they stay in parity.

Idempotent. Run, then rebuild the offline bundle:
    python3 scripts/augment_keywords.py
    python3 scripts/build_offline_bundle.py
"""
import json
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "data" / "compiled" / "drivelegal_graph.json"

SYNONYMS = {
    "SAFETY_NO_HELMET_RIDER": ["bina helmet", "helmet nahi", "without a helmet", "not wearing helmet",
                               "no helmet on", "didn't wear helmet", "did not wear a helmet"],
    "SAFETY_NO_HELMET_PILLION": ["pillion no helmet", "pillion without helmet", "back rider no helmet"],
    "SAFETY_NO_SEATBELT_DRIVER": ["no seat belt", "no seatbelt", "without seatbelt", "without seat belt",
                                  "not wearing seatbelt", "not wearing a seatbelt", "didn't wear seatbelt",
                                  "seatbelt not worn", "belt nahi"],
    "SAFETY_NO_SEATBELT_PASSENGER": ["passenger no seatbelt", "rear seat belt", "back seat belt",
                                     "passenger not wearing seatbelt", "rear passenger seatbelt"],
    "SAFETY_MORE_THAN_2_ON_2W": ["triple riding", "triples", "tripling", "three of us", "three people on a bike",
                                 "three on a bike", "3 people on bike", "three on one bike", "3 on a bike",
                                 "three persons on two wheeler", "teen log bike"],
    "SIGNAL_RED_LIGHT_JUMPING": ["red signal", "jumped signal", "jumped the signal", "jumped a signal",
                                 "signal jump", "signal jumping", "jumped red light", "ran a red light",
                                 "ran the red light", "ran red light", "crossed red light",
                                 "signal tod", "signal tod diya", "jumped the red"],
    "SPEED_OVER_LMV": ["overspeeding", "over speeding", "over speed", "overspeed", "speeding", "speed camera",
                       "speed limit exceeded", "going too fast", "caught speeding", "speed challan",
                       "tez chala raha"],
    "SPEED_OVER_MMV_HMV": ["truck overspeeding", "bus overspeeding", "lorry speeding", "truck speeding",
                           "bus speeding"],
    "SPEED_EXCESSIVE_50PLUS": ["way over the speed limit", "double the speed limit", "50 percent over"],
    "DIR_WRONG_WAY": ["wrong side", "wrong side of the road", "wrong direction", "opposite side of the road",
                      "wrong side driving", "ulta side", "against traffic"],
    "DIR_ONE_WAY_VIOLATION": ["one way", "wrong way on a one way", "entered one way"],
    "DIR_NO_UTURN": ["u turn", "illegal u turn", "no u turn"],
    "DIST_MOBILE_USE": ["on the phone", "talking on phone", "talking on the phone", "using phone", "using my phone",
                        "texting", "mobile while driving", "phone while riding", "phone while driving",
                        "on a call while driving", "mobile phone", "phone pe baat"],
    "DIST_HEADPHONES": ["earphones", "headphones", "airpods", "earbuds"],
    "IMPAIRED_DRUNK": ["drinking", "had been drinking", "was drinking", "drank", "alcohol", "breathalyser",
                       "breath analyser", "breathalyzer", "drunk", "dui", "drink and drive", "drunken driving",
                       "daaru", "sharab", "tipsy", "had a few drinks", "beer"],
    "IMPAIRED_DRUGS": ["drug driving", "drugged driving", "under the influence of drugs", "on drugs",
                       "drugs", "ganja", "weed", "stoned", "high on"],
    "IMPAIRED_REFUSE_TEST": ["refused breathalyser", "refused breath test", "refused to blow",
                             "refused to take the breathalyser", "refused the breathalyser test",
                             "refused breathalyzer", "refused alcohol test"],
    "DOC_NO_DL": ["without a licence", "without a license", "without licence", "without license", "no licence",
                  "no license", "no dl", "without dl", "don't have a licence", "don't have a license",
                  "dont have licence", "dont have license", "bina licence", "bina license",
                  "no driving licence", "no driving license"],
    "DOC_NO_INSURANCE": ["insurance expired", "no insurance", "without insurance", "expired insurance",
                         "uninsured", "insurance lapsed", "insurance not renewed"],
    "DOC_NO_PUC": ["puc expired", "no puc", "without puc", "expired puc", "puc certificate expired",
                   "pollution certificate expired", "pollution certificate", "no pollution certificate"],
    "DOC_NO_RC": ["forgot rc", "forgot the rc", "no rc", "without rc", "rc not with me", "didn't have rc",
                  "didn't have the rc", "registration certificate missing", "rc at home"],
    "DOC_UNDERAGE": ["underage driving", "underage", "minor driving", "minor was driving", "year old son",
                     "year old daughter", "my son was driving", "my daughter was driving", "kid was driving",
                     "under 18 driving", "school kid driving"],
    "JUV_MINOR_DRIVING": ["underage driving", "minor driving", "minor was driving", "my son was driving",
                          "my daughter was driving"],
    "EMERG_NO_WAY_TO_AMBULANCE": ["give way to ambulance", "give way to an ambulance", "didn't give way to an ambulance",
                                  "ambulance behind me", "not giving way to ambulance",
                                  "fire engine", "fire truck"],
    "EMERG_OBSTRUCT_AMBULANCE": ["blocked an ambulance", "blocked ambulance", "blocking ambulance"],
    "NOISE_PRESSURE_HORN": ["pressure horn", "air horn", "musical horn", "multi tone horn", "multi-tone horn"],
    "NOISE_MODIFIED_SILENCER": ["loud exhaust", "modified silencer", "loud silencer", "bullet silencer",
                                "silencer", "silencer is loud",
                                "aftermarket exhaust"],
    "NOISE_CONTINUOUS_HORN": ["honking continuously", "continuous honking", "honking too much"],
    "MOD_TINTED_GLASS": ["black film", "sun film", "sunfilm", "tinted", "tint", "dark film", "window film",
                         "tinted windows", "black glass"],
    "MOD_FANCY_NUMBERPLATE": ["fancy number plate", "style number plate", "stylish number plate"],
    "MOD_HSRP_MISSING": ["no number plate", "without number plate", "no hsrp", "hsrp not installed"],
    "PARK_NO_PARKING": ["towed", "got towed", "no parking", "wrong parking", "parked illegally",
                        "illegal parking", "parking challan"],
    "PARK_FOOTPATH": ["parked on footpath", "parking on footpath", "parked on the sidewalk",
                      "parked on the footpath", "parking on the footpath", "footpath parking"],
    "ACC_NOT_REPORT": ["not reporting an accident", "not reporting accident", "didn't report the accident",
                       "didn't report accident", "failed to report accident", "fine for not reporting"],
    "ACC_HIT_AND_RUN": ["hit and run", "ran away after accident", "fled the scene", "left the scene"],
    "DANGER_RASH_DRIVING": ["rash driving", "reckless driving", "negligent driving", "dangerous driving"],
    "DANGER_STUNT_WHEELIE": ["wheelie", "stunt", "stunts", "drifting", "bike stunt"],
    "DANGER_RACING": ["racing", "street race", "drag race"],
    "FASTAG_NONE": ["no fastag", "without fastag", "fastag blacklisted"],
    "OVERLOAD_PASSENGER": ["too many passengers", "overloaded auto", "extra passengers"],
    "DOC_EXPIRED_RC": ["rc expired", "rc is expired", "expired rc", "registration expired",
                       "registration certificate expired", "rc renewal pending"],
    "COMM_REFUSAL": ["auto driver refused", "auto refused", "taxi refused", "refused to go",
                     "refused the ride", "auto wala refused", "driver refused to take me"],
    "COMM_NO_FARE_METER": ["no fare meter", "meter not working", "auto without meter", "no meter"],
    "COMM_OVERCHARGE": ["overcharged", "charged extra", "asked for more money than the meter"],
    "NOISE_HORN_SILENT_ZONE": ["honked near a hospital", "honking near hospital", "honking near school",
                               "silence zone", "silent zone", "horn near hospital"],
    "MOD_ILLEGAL_LED_HID": ["led lights", "colour changing lights", "color changing lights", "hid lights",
                            "blue lights", "flashing lights", "strobe lights", "extra bright headlights"],
    "SAFETY_NO_AIRBAG": ["removed the airbags", "removed airbags", "airbag removed", "airbags removed",
                         "no airbags", "disabled airbags"],
    "DIR_ILLEGAL_OVERTAKING": ["overtook from the left", "overtaking from the left", "overtook on the left",
                               "overtaking", "overtook", "wrong side overtaking"],
    "OVERLOAD_GOODS_WEIGHT": ["overloaded", "overloading", "overload", "too much load", "excess load"],
    "PARK_DOUBLE_PARKING": ["double parked", "double parking", "parked in the second row"],
    "SPEED_IN_RESIDENTIAL_SCHOOL_ZONE": ["speeding near a school", "speeding near school", "school zone speeding",
                                         "speeding in a residential area", "speeding near hospital"],
    "ACC_DRIVER_FATIGUE": ["hours straight", "driving for 14 hours", "without rest", "driver fatigue",
                           "too many hours", "sleepy driver"],
    "EMIT_OLD_DIESEL_NCR": ["old diesel", "old diesel car", "old petrol car in delhi", "diesel car older than 10"],
    "SAFETY_NO_CHILD_RESTRAINT": ["child on my lap", "kid on my lap", "child on lap", "child in the front seat",
                                  "no child seat", "without child seat", "baby on lap"],
    "SAFETY_NO_CHILD_2W": ["child below 4 on bike", "toddler on bike", "kid on my bike",
                           "child on scooter", "small child on two wheeler"],
    "EV_CHARGING_UNAUTH": ["charged my ev", "ev charging", "public socket", "charging ev illegally"],
    "SCHOOL_BUS_GPS_CAMERA": ["school bus without cctv", "school bus no gps", "school bus cctv",
                              "school bus without gps", "no lady attendant"],
    "DOC_UNAUTHORIZED_USE_VEHICLE": ["gave my car to", "gave my bike to", "lent my car", "lent my bike",
                                     "let my friend drive", "friend who has no licence", "allowed my friend to drive"],
    "SAFETY_NO_SEATBELT_PASSENGER": ["seat belt in the back seat", "seatbelt in the back seat",
                                     "back seat passenger", "rear seat passenger", "passenger without seatbelt"],
    "DIST_EATING_GROOMING": ["eating while driving", "eating while riding", "applying makeup", "eating"],
    "PED_ZEBRA_VIOLATION": ["didn't stop at zebra crossing", "pedestrian crossing", "didn't give way to pedestrian"],
}


def main() -> None:
    g = json.loads(SRC.read_text(encoding="utf-8"))
    added = 0
    for code, extra in SYNONYMS.items():
        node = g["nodes"].get(f"vio:{code}")
        if not node:
            raise SystemExit(f"unknown violation code {code}")
        kws = node.setdefault("keywords", [])
        have = {k.lower() for k in kws}
        for kw in extra:
            if kw.lower() not in have:
                kws.append(kw)
                have.add(kw.lower())
                added += 1
    SRC.write_text(json.dumps(g, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Added {added} keyword synonyms across {len(SYNONYMS)} violations")


if __name__ == "__main__":
    main()
