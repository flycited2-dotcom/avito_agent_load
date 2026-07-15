def pytest_addoption(parser):
    parser.addoption(
        "--regen-golden", action="store_true", default=False,
        help="Перегенерировать golden-файлы снапшот-тестов (просмотрите diff глазами!)")
