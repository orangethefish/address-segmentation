"""Deterministic synthetic benchmark dataset generator for address segmentation.

Synthetic benchmark generation provides controlled reproducibility for stress testing
and latency measurement; synthetic timing does not establish real-world accuracy, and
synthetic labels do not claim broad realistic geographical coverage.

Produces controlled dictionaries, evaluation cases, and manifests with
varied Vietnamese vocabulary, shared prefixes, numeric labels, safe pools,
and trustworthy expected ground-truth labels.
"""

import argparse
import json
from pathlib import Path
import random
import sys
from typing import Any

from address_segmentation.catalog import validate_field_sequence
from address_segmentation.normalize import fold_accents, tokenize
from tools.datasets import canonical_json_hash, sha256_file

# Vietnamese administrative provinces (canonical base)
BASE_PROVINCES: list[str] = [
    "Thành phố Hồ Chí Minh",
    "Thành phố Hà Nội",
    "Thành phố Đà Nẵng",
    "Thành phố Hải Phòng",
    "Thành phố Cần Thơ",
    "Tỉnh An Giang",
    "Tỉnh Bà Rịa - Vũng Tàu",
    "Tỉnh Bắc Giang",
    "Tỉnh Bắc Kạn",
    "Tỉnh Bạc Liêu",
    "Tỉnh Bắc Ninh",
    "Tỉnh Bến Tre",
    "Tỉnh Bình Định",
    "Tỉnh Bình Dương",
    "Tỉnh Bình Phước",
    "Tỉnh Bình Thuận",
    "Tỉnh Cà Mau",
    "Tỉnh Cao Bằng",
    "Tỉnh Đắk Lắk",
    "Tỉnh Đắk Nông",
    "Tỉnh Điện Biên",
    "Tỉnh Đồng Nai",
    "Tỉnh Đồng Tháp",
    "Tỉnh Gia Lai",
    "Tỉnh Hà Giang",
    "Tỉnh Hà Nam",
    "Tỉnh Hà Tĩnh",
    "Tỉnh Hải Dương",
    "Tỉnh Hậu Giang",
    "Tỉnh Hòa Bình",
    "Tỉnh Hưng Yên",
    "Tỉnh Khánh Hòa",
    "Tỉnh Kiên Giang",
    "Tỉnh Kon Tum",
    "Tỉnh Lai Châu",
    "Tỉnh Lâm Đồng",
    "Tỉnh Lạng Sơn",
    "Tỉnh Lào Cai",
    "Tỉnh Long An",
    "Tỉnh Nam Định",
    "Tỉnh Nghệ An",
    "Tỉnh Ninh Bình",
    "Tỉnh Ninh Thuận",
    "Tỉnh Phú Thọ",
    "Tỉnh Phú Yên",
    "Tỉnh Quảng Bình",
    "Tỉnh Quảng Nam",
    "Tỉnh Quảng Ngãi",
    "Tỉnh Quảng Ninh",
    "Tỉnh Quảng Trị",
    "Tỉnh Sóc Trăng",
    "Tỉnh Sơn La",
    "Tỉnh Tây Ninh",
    "Tỉnh Thái Bình",
    "Tỉnh Thái Nguyên",
    "Tỉnh Thanh Hóa",
    "Tỉnh Thừa Thiên Huế",
    "Tỉnh Tiền Giang",
    "Tỉnh Trà Vinh",
    "Tỉnh Tuyên Quang",
    "Tỉnh Vĩnh Long",
    "Tỉnh Vĩnh Phúc",
    "Tỉnh Yên Bái",
]

WARD_PREFIXES: list[str] = ["Phường", "Xã", "Thị trấn"]
WARD_BASE_NAMES: list[str] = [
    "Bến Thành",
    "Bến Nghé",
    "Tân Định",
    "Đa Kao",
    "Cầu Kho",
    "Cầu Ông Lãnh",
    "Cô Giang",
    "Nguyễn Thái Bình",
    "Phạm Ngũ Lão",
    "An Phú",
    "Thảo Điền",
    "An Khánh",
    "Bình An",
    "Bình Trưng Tây",
    "Bình Trưng Đông",
    "Cát Lái",
    "Thạnh Mỹ Lợi",
    "Hiệp Phú",
    "Tăng Nhơn Phú",
    "Phước Long",
    "Linh Trung",
    "Linh Chiểu",
    "Linh Tây",
    "Tam Phú",
    "Hiệp Bình Chánh",
    "Hiệp Bình Phước",
    "Bình Chiểu",
    "Trường Thọ",
    "Linh Xuân",
    "Bình Thọ",
    "Tân Kiên",
    "Tân Nhựt",
    "Bình Chánh",
    "Vĩnh Lộc",
    "Đa Phước",
    "Quy Đức",
    "Hưng Long",
    "An Phú Tây",
    "Phong Phú",
    "Bình Hưng",
    "Phước Kiển",
    "Nhơn Đức",
    "Phú Xuân",
    "Long Thới",
    "Hiệp Phước",
]

WARD_SYLLABLES_A: list[str] = [
    "An", "Bình", "Đức", "Hiệp", "Hòa", "Hưng", "Linh", "Long",
    "Minh", "Nam", "Nghĩa", "Phú", "Quang", "Tân", "Thái", "Thạnh",
    "Trung", "Vĩnh", "Xuân", "Yên",
]
WARD_SYLLABLES_B: list[str] = [
    "An", "Bình", "Chánh", "Đông", "Đức", "Hải", "Hậu", "Hiệp",
    "Hòa", "Hưng", "Khánh", "Kiên", "Lạc", "Lộc", "Long", "Minh",
    "Nghĩa", "Ninh", "Phát", "Phú", "Phước", "Quang", "Tài", "Tâm",
    "Tân", "Tây", "Thành", "Thịnh", "Thọ", "Thuận", "Tiến", "Trung",
    "Vinh", "Vĩnh", "Xuân",
]

STREET_CORE: list[str] = [
    "Nam Kỳ Khởi Nghĩa",
    "Lê Lợi",
    "Trần Hưng Đạo",
    "Hai Bà Trưng",
    "Lê Duẩn",
    "Điện Biên Phủ",
    "Cách Mạng Tháng 8",
    "Hoàng Diệu",
    "Phan Chu Trinh",
    "Võ Thị Sáu",
    "Ngô Quyền",
    "Lý Tự Trọng",
    "Trần Phú",
    "Bạch Đằng",
    "Nguyễn Thị Minh Khai",
    "Pasteur",
    "Đinh Tiên Hoàng",
    "Nguyễn Trãi",
    "Lê Thánh Tôn",
    "Lý Thường Kiệt",
    "Phan Đình Phùng",
    "Trường Chinh",
    "Cộng Hòa",
    "Hoàng Văn Thụ",
    "Nguyễn Văn Trỗi",
    "Xô Viết Nghệ Tĩnh",
    "Phan Đăng Lưu",
    "Nơ Trang Long",
    "Nguyễn Oanh",
    "Quang Trung",
    "Thống Nhất",
    "Phạm Văn Đồng",
    "Võ Văn Kiệt",
    "Mai Chí Thọ",
    "Nguyễn Hữu Thọ",
    "Nguyễn Văn Linh",
    "Huỳnh Tấn Phát",
    "Độc Lập",
    "Tự Do",
    "Dân Chủ",
    "Khởi Nghĩa",
    "Giải Phóng",
    "Đại Cồ Việt",
    "Kim Mã",
    "Giảng Võ",
    "Tràng Thi",
    "Hàng Bài",
    "Hàng Bông",
    "Hàng Gai",
    "Hàng Đào",
    "Phố Huế",
    "Bà Triệu",
    "Tôn Đức Thắng",
    "Nguyễn Lương Bằng",
    "Tây Sơn",
    "Trần Duy Hưng",
    "Phạm Hùng",
    "Khuất Duy Tiến",
    "Nguyễn Xiển",
    "3/2",
    "30/4",
    "19/5",
    "2/9",
]

STREET_FIRST_NAMES: list[str] = [
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng",
    "Bùi", "Đỗ", "Hồ", "Ngô", "Dương", "Lý", "Đào", "Đoàn", "Vương", "Trịnh",
    "Trương", "Đinh", "Lâm", "Phùng", "Mai", "Tô", "Hà", "Chu", "Triệu", "Quách",
    "Tạ", "Thạch", "Lương", "Hứa", "Kim", "Cao", "Bạch", "Thái", "Từ", "Châu",
]

STREET_MIDDLE_NAMES: list[str] = [
    "Văn", "Đình", "Hữu", "Đức", "Thành", "Công", "Minh", "Quang", "Trọng", "Xuân",
    "Thu", "Ngọc", "Bảo", "Phước", "Vĩnh", "Thanh", "Hải", "Tuấn", "Duy", "Hồng",
    "Quốc", "Tấn", "Tiến", "Bình", "Hùng", "Cường", "Trí", "Đăng", "Đạt", "Khánh",
]

STREET_LAST_NAMES: list[str] = [
    "An", "Bình", "Chính", "Dũng", "Đạt", "Đức", "Giang", "Hải", "Hậu", "Hiệp",
    "Hòa", "Hoàng", "Hùng", "Hưng", "Huy", "Khánh", "Khoa", "Kiên", "Lâm", "Linh",
    "Long", "Minh", "Nam", "Nghĩa", "Nguyên", "Nhân", "Phong", "Phú", "Phúc", "Quân",
    "Quang", "Sơn", "Tài", "Tâm", "Thắng", "Thành", "Thảo", "Thịnh", "Thông", "Thuận",
    "Tiến", "Toàn", "Trí", "Trung", "Trường", "Tuấn", "Tùng", "Vinh", "Việt", "Vũ",
]


def generate_synthetic_dictionaries(
    streets_count: int,
    wards_count: int,
    provinces_count: int,
    rng: random.Random,
) -> dict[str, list[str]]:
    """Deterministically generate dictionary labels with rich vocabulary and deliberate collisions."""
    # 1. Provinces
    provinces_set: set[str] = set()
    for prov in BASE_PROVINCES:
        provinces_set.add(prov)
        if len(provinces_set) >= provinces_count:
            break

    # Add cross-field collision label if count allows
    if provinces_count >= 10 and streets_count >= 10:
        provinces_set.add("Hòa Bình")

    prov_counter = 1
    while len(provinces_set) < provinces_count:
        provinces_set.add(f"Tỉnh Vùng Mới {prov_counter}")
        prov_counter += 1

    provinces = sorted(provinces_set)[:provinces_count]

    # 2. Wards
    wards_set: set[str] = set()
    if wards_count >= 10 and streets_count >= 10:
        wards_set.add("Phường Bến Thành")
        wards_set.add("An Phú")

    for base in WARD_BASE_NAMES:
        if len(wards_set) >= wards_count:
            break
        for prefix in WARD_PREFIXES:
            wards_set.add(f"{prefix} {base}")
            if len(wards_set) >= wards_count:
                break

    for num in range(1, 100):
        if len(wards_set) >= wards_count:
            break
        wards_set.add(f"Phường {num}")

    for s_a in WARD_SYLLABLES_A:
        if len(wards_set) >= wards_count:
            break
        for s_b in WARD_SYLLABLES_B:
            if len(wards_set) >= wards_count:
                break
            for prefix in WARD_PREFIXES:
                wards_set.add(f"{prefix} {s_a} {s_b}")
                if len(wards_set) >= wards_count:
                    break

    ward_counter = 1
    while len(wards_set) < wards_count:
        wards_set.add(f"Phường Khu Vực {ward_counter}")
        ward_counter += 1

    wards = sorted(wards_set)[:wards_count]

    # 3. Streets
    streets_set: set[str] = set()
    if streets_count >= 10:
        if provinces_count >= 10 and "Hòa Bình" in provinces:
            streets_set.add("Hòa Bình")
            streets_set.add("Hóa Bình")
        if wards_count >= 10 and "Phường Bến Thành" in wards:
            streets_set.add("Bến Thành")

    for s in STREET_CORE:
        if len(streets_set) >= streets_count:
            break
        streets_set.add(s)

    for num in range(1, 201):
        if len(streets_set) >= streets_count:
            break
        streets_set.add(f"Đường số {num}")
        if len(streets_set) >= streets_count:
            break
        streets_set.add(f"Phố số {num}")

    prefixes = ["Đường", "Phố", ""]
    suffixes = ["", "Nối Dài", "Bắc", "Nam", "Đông", "Tây"]

    for fn in STREET_FIRST_NAMES:
        if len(streets_set) >= streets_count:
            break
        for mn in STREET_MIDDLE_NAMES:
            if len(streets_set) >= streets_count:
                break
            for ln in STREET_LAST_NAMES:
                if len(streets_set) >= streets_count:
                    break
                full_name = f"{fn} {mn} {ln}"
                for pref in prefixes:
                    if len(streets_set) >= streets_count:
                        break
                    for suff in suffixes:
                        label_parts = [p for p in (pref, full_name, suff) if p]
                        streets_set.add(" ".join(label_parts))
                        if len(streets_set) >= streets_count:
                            break

    street_counter = 1
    while len(streets_set) < streets_count:
        streets_set.add(f"Đường Thôn Bản {street_counter}")
        street_counter += 1

    streets = sorted(streets_set)[:streets_count]

    validate_field_sequence(streets, "streets")
    validate_field_sequence(wards, "wards")
    validate_field_sequence(provinces, "provinces")

    return {
        "streets": streets,
        "wards": wards,
        "provinces": provinces,
    }


def analyze_dictionary_pools(
    dictionaries: dict[str, list[str]],
) -> tuple[dict[str, list[str]], list[str], list[list[str]]]:
    """Analyze dictionary to construct safe pools and identify proven collisions.

    Returns:
    - safe_pools: mapping field -> list of labels whose folded key is unique in field
      and has no cross-field collisions.
    - proven_cross_field: labels present across multiple fields.
    - proven_street_folded_collisions: groups of >=2 streets with identical accent-folding.
    """
    fields = ("street", "ward", "province")
    prefixes = {"street": (("duong",), ("d",)),
                "ward": (("phuong",), ("xa",), ("thi", "tran"), ("p",)),
                "province": (("thanh", "pho"), ("tinh",), ("tp",))}
    keys = {}
    by_key = {}
    for field in fields:
        for label in dictionaries[field + "s"]:
            full = tuple(t.folded for t in tokenize(label))
            body = full
            for prefix in prefixes[field]:
                if full[:len(prefix)] == prefix and len(full) > len(prefix):
                    body = full[len(prefix):]
                    break
            keys[field, label] = (full, body)
            for key in {full, body}:
                by_key.setdefault(key, set()).add((field, label))
    pools = {f: [] for f in fields}
    for (field, label), (full, body) in keys.items():
        if any(len(by_key[key]) != 1 for key in {full, body}):
            continue
        cross_field = any(other_field != field
            for start in range(len(full)) for end in range(start+1, len(full)+1)
            for other_field, _ in by_key.get(full[start:end], ()))
        if not cross_field:
            pools[field].append(label)
    cross = sorted(set(dictionaries["streets"]) & set(dictionaries["provinces"]))
    folded_groups = {}
    for label in dictionaries["streets"]:
        folded_groups.setdefault(fold_accents(label), []).append(label)
    collisions = [sorted(group) for key, group in folded_groups.items()
                  if len(group) > 1 and all(label.casefold() != key for label in group)]
    return pools, cross, collisions


def generate_synthetic_cases(
    cases_count: int,
    dictionaries: dict[str, list[str]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Deterministically generate cases with varied categories and trustworthy ground-truth expectations."""
    safe_pools, proven_cross_field, proven_street_folded_collisions = analyze_dictionary_pools(
        dictionaries
    )
    safe_streets = safe_pools["street"]
    safe_wards = safe_pools["ward"]
    safe_provinces = safe_pools["province"]

    has_all_safe_pools = (
        len(safe_streets) > 0 and len(safe_wards) > 0 and len(safe_provinces) > 0
    )

    n_exact = int(cases_count * 0.40)
    n_accentless = int(cases_count * 0.20)
    n_no_match = int(cases_count * 0.15)
    n_ambiguity = int(cases_count * 0.10)
    n_repeated = int(cases_count * 0.08)
    n_adversarial = cases_count - (
        n_exact + n_accentless + n_no_match + n_ambiguity + n_repeated
    )

    cases: list[dict[str, Any]] = []
    case_idx = 1

    # 1. Exact cases
    for i in range(n_exact):
        cid = f"synth-case-{case_idx:05d}"
        if has_all_safe_pools:
            s = rng.choice(safe_streets)
            w = rng.choice(safe_wards)
            p = rng.choice(safe_provinces)
            num = (i % 150) + 1
            pattern = i % 4
            if pattern == 0:
                addr = f"Số {num} Đường {s}, {w}, {p}"
                exp = {"street": s, "ward": w, "province": p}
                tags = ["exact", "full", "synthetic"]
            elif pattern == 1:
                addr = f"Số {num} Đường {s}, {p}"
                exp = {"street": s, "ward": None, "province": p}
                tags = ["exact", "missing-ward", "synthetic"]
            elif pattern == 2:
                addr = f"{w}, {p}"
                exp = {"street": None, "ward": w, "province": p}
                tags = ["exact", "missing-street", "synthetic"]
            else:
                addr = f"Số {num} Đường {s}, {w}"
                exp = {"street": s, "ward": w, "province": None}
                tags = ["exact", "missing-province", "synthetic"]
            kind = "recoverable"
        else:
            addr = f"Số 999 Cung Đường Không Xác Định {case_idx}, Khu Vực Trống"
            exp = {"street": None, "ward": None, "province": None}
            tags = ["unsupported", "absent-safe-pool", "synthetic"]
            kind = "unsupported"

        cases.append(
            {
                "id": cid,
                "address": addr,
                "expected": exp,
                "tags": tags,
                "group_id": f"group-exact-{i // 10}",
                "expectation_kind": kind,
            }
        )
        case_idx += 1

    # 2. Accentless cases
    for i in range(n_accentless):
        cid = f"synth-case-{case_idx:05d}"
        if has_all_safe_pools:
            s = rng.choice(safe_streets)
            w = rng.choice(safe_wards)
            p = rng.choice(safe_provinces)
            num = (i % 150) + 1
            raw_addr = f"Số {num} Đường {s}, {w}, {p}"
            addr = fold_accents(raw_addr)
            exp = {"street": s, "ward": w, "province": p}
            tags = ["accentless", "folded", "synthetic"]
            kind = "recoverable"
        else:
            addr = f"So 999 Cung Duong Khong Xac Dinh {case_idx}, Khu Vuc Trong"
            exp = {"street": None, "ward": None, "province": None}
            tags = ["unsupported", "absent-safe-pool", "synthetic"]
            kind = "unsupported"

        cases.append(
            {
                "id": cid,
                "address": addr,
                "expected": exp,
                "tags": tags,
                "group_id": f"group-accentless-{i // 10}",
                "expectation_kind": kind,
            }
        )
        case_idx += 1

    # 3. No match cases
    for i in range(n_no_match):
        cid = f"synth-case-{case_idx:05d}"
        num = (i * 99) + 7
        addr = f"Số {num} Cung Đường Ngoại Lai Không Xác Định {case_idx}, Khu Vực Xa Lạ 99999"
        exp = {"street": None, "ward": None, "province": None}

        cases.append(
            {
                "id": cid,
                "address": addr,
                "expected": exp,
                "tags": ["unsupported", "no-match", "synthetic"],
                "group_id": f"group-unsupported-{i // 10}",
                "expectation_kind": "unsupported",
            }
        )
        case_idx += 1

    # 4. Ambiguity cases
    for i in range(n_ambiguity):
        cid = f"synth-case-{case_idx:05d}"
        if proven_cross_field and len(safe_wards) > 0:
            c_label = rng.choice(proven_cross_field)
            w = rng.choice(safe_wards)
            addr = f"Số 123 {c_label}, {w}"
            exp = {"street": None, "ward": w, "province": None}
            tags = ["collision", "cross-field", "ambiguity", "synthetic"]
            kind = "ambiguous"
        elif proven_street_folded_collisions and len(safe_wards) > 0 and len(safe_provinces) > 0:
            group = rng.choice(proven_street_folded_collisions)
            folded_s = fold_accents(group[0])
            w = rng.choice(safe_wards)
            p = rng.choice(safe_provinces)
            addr = f"Đường {folded_s}, {w}, {p}"
            exp = {"street": None, "ward": w, "province": p}
            tags = ["collision", "folded-ambiguity", "ambiguity", "synthetic"]
            kind = "ambiguous"
        elif len(safe_streets) >= 2 and len(safe_wards) > 0 and len(safe_provinces) > 0:
            s1, s2 = rng.sample(safe_streets, 2)
            w = rng.choice(safe_wards)
            p = rng.choice(safe_provinces)
            addr = f"Góc Đường {s1} và Đường {s2}, {w}, {p}"
            exp = {"street": None, "ward": w, "province": p}
            tags = ["collision", "same-field", "ambiguity", "synthetic"]
            kind = "ambiguous"
        else:
            addr = f"Địa điểm chưa phân loại không thuộc danh mục {case_idx}"
            exp = {"street": None, "ward": None, "province": None}
            tags = ["unsupported", "absent-ambiguity-pool", "synthetic"]
            kind = "unsupported"

        cases.append(
            {
                "id": cid,
                "address": addr,
                "expected": exp,
                "tags": tags,
                "group_id": f"group-ambiguous-{i // 10}",
                "expectation_kind": kind,
            }
        )
        case_idx += 1

    # 5. Repeated token cases
    for i in range(n_repeated):
        cid = f"synth-case-{case_idx:05d}"
        if has_all_safe_pools:
            s = rng.choice(safe_streets)
            w = rng.choice(safe_wards)
            p = rng.choice(safe_provinces)
            addr = f"Đường {s} {s}, {w}, {p}"
            exp = {"street": s, "ward": w, "province": p}
            tags = ["repeated-token", "synthetic"]
            kind = "recoverable"
        else:
            addr = f"Khu Vực Lặp Lại Không Rõ Danh Tính {case_idx}"
            exp = {"street": None, "ward": None, "province": None}
            tags = ["unsupported", "absent-safe-pool", "synthetic"]
            kind = "unsupported"

        cases.append(
            {
                "id": cid,
                "address": addr,
                "expected": exp,
                "tags": tags,
                "group_id": f"group-repeated-{i // 10}",
                "expectation_kind": kind,
            }
        )
        case_idx += 1

    # 6. Long adversarial cases
    for i in range(n_adversarial):
        cid = f"synth-case-{case_idx:05d}"
        if has_all_safe_pools:
            s = rng.choice(safe_streets)
            w = rng.choice(safe_wards)
            p = rng.choice(safe_provinces)
            addr = (
                f"Tòa nhà Landmark 81 tầng số 45/12B ngõ 102 hẻm 3, Đường {s}, "
                f"đối diện siêu thị Vincom, gần ngã tư đèn tín hiệu, {w}, {p}, "
                f"Việt Nam, SĐT liên hệ 0912345678"
            )
            exp = {"street": s, "ward": w, "province": p}
            tags = ["long-adversarial", "stress", "synthetic"]
            kind = "recoverable"
        else:
            addr = (
                f"Tòa nhà Landmark 81 tầng số 45/12B ngõ 102 hẻm 3, "
                f"Khu Vực Không Xác Định Số {case_idx}, SĐT 0912345678"
            )
            exp = {"street": None, "ward": None, "province": None}
            tags = ["unsupported", "absent-safe-pool", "synthetic"]
            kind = "unsupported"

        cases.append(
            {
                "id": cid,
                "address": addr,
                "expected": exp,
                "tags": tags,
                "group_id": f"group-adversarial-{i // 10}",
                "expectation_kind": kind,
            }
        )
        case_idx += 1

    return cases


def generate_benchmark_dataset(
    output_dir: Path,
    seed: int = 42,
    streets_count: int = 23800,
    wards_count: int = 340,
    provinces_count: int = 34,
    cases_count: int = 1000,
) -> dict[str, Any]:
    """Generate deterministic benchmark dataset and write dictionary, cases, and manifest."""
    if streets_count <= 0 or wards_count <= 0 or provinces_count <= 0 or cases_count <= 0:
        raise ValueError(
            "streets, wards, provinces, and cases counts must all be positive integers"
        )

    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    dict_path = output_dir / "dictionary.json"
    cases_path = output_dir / "cases.jsonl"
    manifest_path = output_dir / "manifest.json"

    dictionaries = generate_synthetic_dictionaries(
        streets_count=streets_count,
        wards_count=wards_count,
        provinces_count=provinces_count,
        rng=rng,
    )

    with open(dict_path, "w", encoding="utf-8") as f:
        json.dump(dictionaries, f, ensure_ascii=False, indent=2)
        f.write("\n")

    cases = generate_synthetic_cases(
        cases_count=cases_count,
        dictionaries=dictionaries,
        rng=rng,
    )

    with open(cases_path, "w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    dict_hash = sha256_file(dict_path)
    cases_hash = sha256_file(cases_path)

    manifest = {
        "provenance": "synthetic-controlled-benchmark-generator-stage-b",
        "documentation": (
            "Synthetic benchmark generation provides controlled reproducibility for stress "
            "testing and latency measurement; synthetic timing does not establish real-world "
            "accuracy, and synthetic labels do not claim broad realistic geographical coverage."
        ),
        "seed": seed,
        "counts": {
            "streets": len(dictionaries["streets"]),
            "wards": len(dictionaries["wards"]),
            "provinces": len(dictionaries["provinces"]),
            "cases": len(cases),
        },
        "hashes": {
            "dictionary": dict_hash,
            "cases": cases_hash,
        },
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return manifest


def main(argv: list[str] | None = None) -> int:
    """CLI driver for benchmark generator."""
    parser = argparse.ArgumentParser(
        description="Address segmentation synthetic benchmark generator."
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write dictionary.json, cases.jsonl, and manifest.json.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic PRNG seed (default: 42).",
    )
    parser.add_argument(
        "--streets",
        type=int,
        default=23800,
        help="Number of synthetic street labels (default: 23800).",
    )
    parser.add_argument(
        "--wards",
        type=int,
        default=340,
        help="Number of synthetic ward labels (default: 340).",
    )
    parser.add_argument(
        "--provinces",
        type=int,
        default=34,
        help="Number of synthetic province labels (default: 34).",
    )
    parser.add_argument(
        "--cases",
        type=int,
        default=1000,
        help="Number of synthetic test cases (default: 1000).",
    )

    args = parser.parse_args(argv)

    try:
        out_path = Path(args.output_dir)
        if not out_path.is_absolute():
            out_path = Path.cwd() / out_path

        generate_benchmark_dataset(
            output_dir=out_path,
            seed=args.seed,
            streets_count=args.streets,
            wards_count=args.wards,
            provinces_count=args.provinces,
            cases_count=args.cases,
        )
    except (ValueError, TypeError, OSError) as exc:
        sys.stderr.write(f"Generator error: {exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
