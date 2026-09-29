from scripts.cache_avito_category_schemas import selected_leaves


def test_selected_leaves_include_household_and_tv_only():
    tree = {"categories": [
        {"name": "Для дома и дачи", "nested": [
            {"name": "Бытовая техника", "slug": "appliances", "nested": [
                {"name": "Для кухни", "nested": [
                    {"name": "Чайники", "slug": "kettles"},
                ]},
            ]},
        ]},
        {"name": "Электроника", "nested": [
            {"name": "Аудио и видео", "nested": [
                {"name": "Телевизоры и проекторы", "nested": [
                    {"name": "Телевизоры", "slug": "televisions"},
                    {"name": "Проекторы", "slug": "projectors"},
                ]},
            ]},
        ]},
        {"name": "Транспорт", "nested": [{"name": "Авто", "slug": "cars"}]},
    ]}

    assert selected_leaves(tree) == [
        ("kettles", ("Для дома и дачи", "Бытовая техника", "Для кухни", "Чайники")),
        ("televisions", ("Электроника", "Аудио и видео", "Телевизоры и проекторы", "Телевизоры")),
    ]
