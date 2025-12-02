from text_utils import slugify_group_name


def test_slugify_group_name_transliterates_and_cleans():
    assert slugify_group_name("15.14д-гг01/24м") == "15-14d-gg01-24m"
    assert slugify_group_name("  группа А-1  ") == "gruppa-a-1"
