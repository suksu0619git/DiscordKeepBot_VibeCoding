"""FR-1 크레딧 포맷 단위 테스트 — 요구사항 예시와 100% 일치하는지 검증한다."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.credit_format import (  # noqa: E402
    OPTIONAL_LABELS,
    OPTIONAL_ROLES,
    CreditData,
    format_credit,
    parse_names,
    parse_tags,
    people_from_names,
    wrap_codeblock,
)

# 요구사항 FR-1.1 의 출력 예시 원문 (줄바꿈/공백 포함 그대로)
EXPECTED = """제목 : VRC Unity Noob vs Pro
Production : AT_Cat, RUCOO
Edit : 으름__
3D : suksu0619
Filming : AT_Cat
Act : 잉어에요, 깜 냐 옹

MUSIC
(https://youtu.be/zql8G-4gE7w?si=NSFlwxqvd9kIZb0i)

World
Studio KEEP - Studio KEEP KEXCO

태그 : vrchat, 쇼팽, 유니티, Unity, noob, pro, 꿀팁, 추천, kipfel, shorts"""


def sample_data(**overrides) -> CreditData:
    data = CreditData(
        title="VRC Unity Noob vs Pro",
        production=people_from_names("AT_Cat, RUCOO"),
        edit=people_from_names("으름__"),
        three_d=people_from_names("suksu0619"),
        filming=people_from_names("AT_Cat"),
        act=people_from_names("잉어에요, 깜 냐 옹"),
        music_link="https://youtu.be/zql8G-4gE7w?si=NSFlwxqvd9kIZb0i",
        world="Studio KEEP - Studio KEEP KEXCO",
        tags=parse_tags("vrchat, 쇼팽, 유니티, Unity, noob, pro, 꿀팁, 추천, kipfel, shorts"),
    )
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


class FormatCreditTest(unittest.TestCase):
    def test_matches_requirement_example_exactly(self):
        self.assertEqual(format_credit(sample_data()), EXPECTED)

    def test_line_structure(self):
        lines = format_credit(sample_data()).split("\n")
        self.assertEqual(len(lines), 14)
        self.assertEqual(lines[6], "")  # Act 다음 빈 줄
        self.assertEqual(lines[7], "MUSIC")
        self.assertEqual(lines[9], "")  # 링크 다음 빈 줄
        self.assertEqual(lines[10], "World")
        self.assertEqual(lines[12], "")  # World 다음 빈 줄
        self.assertTrue(lines[13].startswith("태그 : "))
        self.assertNotIn("#", lines[13])

    def test_no_trailing_newline(self):
        self.assertFalse(format_credit(sample_data()).endswith("\n"))

    def test_label_separator_is_space_colon_space(self):
        self.assertTrue(format_credit(sample_data()).startswith("제목 : "))

    def test_input_whitespace_is_trimmed(self):
        data = sample_data(
            title="  VRC Unity Noob vs Pro  ",
            production=people_from_names("  AT_Cat ,  RUCOO  "),
            world="  Studio KEEP - Studio KEEP KEXCO  ",
        )
        self.assertEqual(format_credit(data), EXPECTED)

    def test_world_has_no_braces(self):
        self.assertIn("\nWorld\nStudio KEEP - Studio KEEP KEXCO\n", format_credit(sample_data()))

    def test_world_braces_in_input_are_stripped(self):
        data = sample_data(world="{Studio KEEP - Studio KEEP KEXCO}")
        self.assertEqual(format_credit(data), EXPECTED)

    def test_link_is_wrapped_in_parentheses(self):
        self.assertIn("\n(https://youtu.be/", format_credit(sample_data()))

    def test_link_already_parenthesized_is_not_double_wrapped(self):
        data = sample_data(music_link="(https://youtu.be/zql8G-4gE7w?si=NSFlwxqvd9kIZb0i)")
        self.assertEqual(format_credit(data), EXPECTED)

    def test_tags_are_comma_joined_without_hash(self):
        rendered = format_credit(sample_data())
        self.assertTrue(rendered.endswith("kipfel, shorts"))
        self.assertNotIn("#", rendered)

    def test_single_name_has_no_separator(self):
        self.assertIn("\nEdit : 으름__\n", format_credit(sample_data()))


class OptionalFieldFormatTest(unittest.TestCase):
    """비어 있는 선택 항목은 줄 자체가 빠진다(빈 `3D : ` 줄을 남기지 않는다)."""

    def test_empty_three_d_line_is_dropped(self):
        body = format_credit(sample_data(three_d=[]))
        self.assertNotIn("3D", body)
        # 앞뒤 줄은 그대로 붙어 있어야 한다.
        self.assertIn("Edit : 으름__\nFilming : AT_Cat\n", body)

    def test_dropping_a_role_shortens_the_body_by_one_line(self):
        full = format_credit(sample_data()).split("\n")
        without = format_credit(sample_data(three_d=[])).split("\n")
        self.assertEqual(len(full) - len(without), 1)

    def test_every_optional_role_can_be_empty_at_once(self):
        lines = format_credit(sample_data(three_d=[], filming=[], act=[])).split("\n")
        self.assertEqual(
            lines[:3],
            ["제목 : VRC Unity Noob vs Pro", "Production : AT_Cat, RUCOO", "Edit : 으름__"],
        )
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], "MUSIC")

    def test_empty_link_drops_the_whole_music_block(self):
        body = format_credit(sample_data(music_link=""))
        self.assertNotIn("MUSIC", body)
        self.assertIn("Act : 잉어에요, 깜 냐 옹\n\nWorld\n", body)

    def test_required_fields_still_render_when_optionals_are_empty(self):
        body = format_credit(
            sample_data(three_d=[], filming=[], act=[], music_link="")
        )
        self.assertEqual(
            body,
            "제목 : VRC Unity Noob vs Pro\n"
            "Production : AT_Cat, RUCOO\n"
            "Edit : 으름__\n"
            "\n"
            "World\n"
            "Studio KEEP - Studio KEEP KEXCO\n"
            "\n"
            "태그 : vrchat, 쇼팽, 유니티, Unity, noob, pro, 꿀팁, 추천, kipfel, shorts",
        )


class ParseNamesTest(unittest.TestCase):
    def test_comma_separated(self):
        self.assertEqual(parse_names("AT_Cat, RUCOO"), ["AT_Cat", "RUCOO"])

    def test_internal_spaces_are_preserved(self):
        # "깜 냐 옹" 은 공백이 포함된 하나의 닉네임이다.
        self.assertEqual(parse_names("잉어에요, 깜 냐 옹"), ["잉어에요", "깜 냐 옹"])

    def test_newline_separated(self):
        self.assertEqual(parse_names("AT_Cat\nRUCOO"), ["AT_Cat", "RUCOO"])

    def test_fullwidth_comma(self):
        self.assertEqual(parse_names("AT_Cat，RUCOO"), ["AT_Cat", "RUCOO"])

    def test_empty_chunks_are_dropped(self):
        self.assertEqual(parse_names("AT_Cat,,  , RUCOO,"), ["AT_Cat", "RUCOO"])

    def test_duplicates_are_removed_keeping_order(self):
        self.assertEqual(parse_names("AT_Cat, RUCOO, AT_Cat"), ["AT_Cat", "RUCOO"])

    def test_blank_input(self):
        self.assertEqual(parse_names("   "), [])
        self.assertEqual(parse_names(""), [])


class ParseTagsTest(unittest.TestCase):
    def test_hash_prefix_is_stripped(self):
        self.assertEqual(parse_tags("#vrchat #쇼팽"), ["vrchat", "쇼팽"])

    def test_comma_separated_without_hash(self):
        self.assertEqual(parse_tags("vrchat, 쇼팽, 유니티"), ["vrchat", "쇼팽", "유니티"])

    def test_mixed_separators(self):
        self.assertEqual(parse_tags("vrchat, 쇼팽 #유니티\nUnity"), ["vrchat", "쇼팽", "유니티", "Unity"])

    def test_duplicates_removed(self):
        self.assertEqual(parse_tags("#shorts shorts"), ["shorts"])

    def test_blank_input(self):
        self.assertEqual(parse_tags(""), [])


class ValidationTest(unittest.TestCase):
    def test_complete_data_has_no_missing_labels(self):
        self.assertEqual(sample_data().missing_labels(), [])

    def test_empty_string_field_is_missing(self):
        self.assertEqual(sample_data(title="").missing_labels(), ["제목"])

    def test_whitespace_only_field_is_missing(self):
        self.assertEqual(sample_data(world="   ").missing_labels(), ["World"])

    def test_empty_list_field_is_missing(self):
        self.assertEqual(sample_data(production=[]).missing_labels(), ["Production"])

    def test_multiple_missing_labels_are_reported_in_order(self):
        data = sample_data(title="", edit=[], tags=[])
        self.assertEqual(data.missing_labels(), ["제목", "Edit", "태그"])

    def test_empty_data_reports_every_required_label(self):
        self.assertEqual(
            CreditData().missing_labels(),
            ["제목", "Production", "Edit", "World", "태그"],
        )

    def test_optional_labels_are_never_missing(self):
        # 3D · Filming · Act · 음악 링크 는 비어도 제출을 막지 않는다.
        data = sample_data(three_d=[], filming=[], act=[], music_link="")
        self.assertEqual(data.missing_labels(), [])

    def test_optional_labels_cover_the_intended_items(self):
        self.assertEqual(OPTIONAL_ROLES, ("3D", "Filming", "Act"))
        self.assertEqual(OPTIONAL_LABELS, ("3D", "Filming", "Act", "음악 링크"))


class NamesByRoleTest(unittest.TestCase):
    def test_all_five_roles_are_present(self):
        mapping = sample_data().names_by_role()
        self.assertEqual(
            sorted(mapping), sorted(["Production", "Edit", "3D", "Filming", "Act"])
        )

    def test_role_names_map_to_submitted_nicknames(self):
        mapping = sample_data().names_by_role()
        self.assertEqual(mapping["Production"], ["AT_Cat", "RUCOO"])
        self.assertEqual(mapping["3D"], ["suksu0619"])
        self.assertEqual(mapping["Act"], ["잉어에요", "깜 냐 옹"])

    def test_all_names_deduplicates_across_roles(self):
        # AT_Cat 은 Production 과 Filming 에 모두 등장한다.
        self.assertEqual(
            sample_data().all_names(),
            ["AT_Cat", "RUCOO", "으름__", "suksu0619", "잉어에요", "깜 냐 옹"],
        )


class CodeblockTest(unittest.TestCase):
    def test_wraps_with_text_codeblock(self):
        wrapped = wrap_codeblock("hello")
        self.assertEqual(wrapped, "```text\nhello\n```")

    def test_example_stays_intact_inside_codeblock(self):
        wrapped = wrap_codeblock(format_credit(sample_data()))
        self.assertTrue(wrapped.startswith("```text\n제목 : "))
        self.assertTrue(wrapped.endswith("kipfel, shorts\n```"))
        self.assertEqual(wrapped.count("```"), 2)

    def test_embedded_backticks_do_not_break_the_block(self):
        wrapped = wrap_codeblock("a```b")
        self.assertEqual(wrapped.count("```"), 2)

    def test_result_fits_discord_message_limit(self):
        self.assertLess(len(wrap_codeblock(format_credit(sample_data()))), 2000)


if __name__ == "__main__":
    unittest.main()
