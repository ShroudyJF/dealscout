def test_package_importable():
    import dealscout

    assert dealscout.__version__ == "0.1.0"
