from tastytrade_mcp import credentials


def test_set_get_roundtrip():
    credentials.set_secret(credentials.CLIENT_SECRET, "abc", sandbox=True)
    assert credentials.get_secret(credentials.CLIENT_SECRET, sandbox=True) == "abc"


def test_environments_are_namespaced():
    credentials.set_secret(credentials.REFRESH_TOKEN, "prod-tok", sandbox=False)
    credentials.set_secret(credentials.REFRESH_TOKEN, "sand-tok", sandbox=True)
    assert credentials.get_secret(credentials.REFRESH_TOKEN, sandbox=False) == "prod-tok"
    assert credentials.get_secret(credentials.REFRESH_TOKEN, sandbox=True) == "sand-tok"


def test_secrets_present_and_missing():
    assert not credentials.secrets_present(sandbox=True)
    assert set(credentials.missing_secrets(sandbox=True)) == {
        credentials.CLIENT_SECRET,
        credentials.REFRESH_TOKEN,
    }
    credentials.set_secret(credentials.CLIENT_SECRET, "a", sandbox=True)
    credentials.set_secret(credentials.REFRESH_TOKEN, "b", sandbox=True)
    assert credentials.secrets_present(sandbox=True)


def test_delete_secret():
    credentials.set_secret(credentials.CLIENT_SECRET, "x", sandbox=True)
    assert credentials.delete_secret(credentials.CLIENT_SECRET, sandbox=True)
    assert not credentials.delete_secret(credentials.CLIENT_SECRET, sandbox=True)
