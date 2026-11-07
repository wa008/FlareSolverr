import os
import io
import json
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import utils


class BrowserStartupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.created = []
        self.commands = []
        self.fail_command = None
        self.fail_constructor = False

        def construct(driver, **kwargs):
            self.created.append(driver)
            driver.options = kwargs['options']
            if self.fail_constructor:
                raise RuntimeError('constructor failed')

        def execute(driver, command, params):
            # The authentication worker must still be loadable during setup.
            self.assertTrue(os.path.isfile(os.path.join(driver._proxy_extension_dir, 'flaresolverr_proxy_auth.js')))
            self.commands.append(command)
            if command == self.fail_command:
                raise TimeoutError('simulated CDP timeout')

        self.stack.enter_context(patch.object(utils.uc.Chrome, '__init__', construct))
        self.quit_mock = self.stack.enter_context(patch.object(utils.uc.Chrome, 'quit', autospec=True))
        self.stack.enter_context(patch.object(utils.ManagedChrome, 'execute_cdp_cmd', execute))
        self.readiness_mock = self.stack.enter_context(patch.object(utils, 'wait_for_proxy_extension'))
        self.stack.enter_context(patch.object(utils, 'get_chrome_exe_path', return_value='/usr/bin/chromium'))
        self.stack.enter_context(patch.object(utils, 'start_xvfb_display'))
        self.stack.enter_context(patch.object(utils, 'get_config_headless', return_value=True))
        self.stack.enter_context(patch.object(utils.os.path, 'exists', return_value=True))
        self.stack.enter_context(patch.object(utils.os.path, 'isdir', return_value=False))
        self.proxy = {'url': 'https://proxy.invalid:12321', 'username': 'test', 'password': 'test'}

    def test_extension_lives_until_quit_and_features_are_combined(self):
        driver = utils.get_webdriver(self.proxy)
        directory = driver._proxy_extension_dir
        self.assertEqual(self.commands, ['Network.setBlockedURLs', 'Network.enable'])
        self.assertTrue(os.path.isfile(os.path.join(directory, 'flaresolverr_proxy_auth.js')))
        self.readiness_mock.assert_called_once_with(driver, self.proxy)
        self.assertIn('about:blank', driver.options.arguments)
        flags = [a for a in driver.options.arguments if a.startswith('--disable-features=')]
        self.assertEqual(len(flags), 1)
        self.assertIn('LocalNetworkAccessChecks', flags[0])
        self.assertIn('DisableLoadExtensionCommandLineSwitch', flags[0])
        driver.quit()
        self.assertFalse(os.path.isfile(os.path.join(directory, 'flaresolverr_proxy_auth.js')))
        self.quit_mock.assert_called_once_with(driver)

    def test_each_post_creation_failure_closes_browser(self):
        for command in ('Network.setBlockedURLs', 'Network.enable'):
            with self.subTest(command=command):
                self.fail_command = command
                self.quit_mock.reset_mock()
                with self.assertLogs(level='ERROR'), self.assertRaisesRegex(TimeoutError, 'CDP timeout'):
                    utils.get_webdriver(self.proxy)
                driver = self.created[-1]
                self.quit_mock.assert_called_once_with(driver)
                self.assertIsNone(driver._proxy_extension_dir)

    def test_constructor_failure_closes_partial_browser(self):
        self.fail_constructor = True
        with self.assertLogs(level='ERROR'), self.assertRaisesRegex(RuntimeError, 'constructor failed'):
            utils.get_webdriver(self.proxy)
        driver = self.created[-1]
        self.quit_mock.assert_called_once_with(driver)
        self.assertIsNone(driver._proxy_extension_dir)

    def test_unready_proxy_fails_before_navigation_and_cleans_up(self):
        self.readiness_mock.side_effect = TimeoutError('proxy not ready')
        with self.assertLogs(level='ERROR'), self.assertRaisesRegex(TimeoutError, 'proxy not ready'):
            utils.get_webdriver(self.proxy)
        driver = self.created[-1]
        self.quit_mock.assert_called_once_with(driver)
        self.assertIsNone(driver._proxy_extension_dir)

    def test_extension_removed_even_if_underlying_quit_raises(self):
        driver = utils.get_webdriver(self.proxy)
        directory = driver._proxy_extension_dir
        self.quit_mock.side_effect = RuntimeError('quit failed')
        with self.assertRaisesRegex(RuntimeError, 'quit failed'):
            driver.quit()
        self.quit_mock.side_effect = None
        self.assertFalse(os.path.isfile(os.path.join(directory, 'flaresolverr_proxy_auth.js')))


class ProxyReadinessTests(unittest.TestCase):
    def check_state(self, state):
        driver = MagicMock()
        driver.capabilities = {'goog:chromeOptions': {'debuggerAddress': '127.0.0.1:1234'}}
        targets = [{'type': 'service_worker', 'url': 'chrome-extension://test/flaresolverr_proxy_auth.js',
                    'webSocketDebuggerUrl': 'ws://127.0.0.1:1234/devtools/page/test'}]
        opener = MagicMock()
        opener.open.return_value = io.BytesIO(json.dumps(targets).encode())
        socket = MagicMock()
        socket.recv.side_effect = [json.dumps({'id': 1, 'result': {}}),
                                  json.dumps({'id': 2, 'result': {}}),
                                  json.dumps({'id': 3, 'result': {'result': {'value': state}}})]
        with patch.object(utils.urllib.request, 'build_opener', return_value=opener), \
                patch.object(utils, 'connect') as connect:
            connect.return_value.__enter__.return_value = socket
            utils.wait_for_proxy_extension(driver, {'url': 'https://proxy.invalid:12321'})

    def valid_state(self):
        return {'level': 'controlled_by_this_extension', 'authListener': True,
                'value': {'mode': 'fixed_servers', 'rules': {'singleProxy': {
                    'host': 'proxy.invalid', 'port': 12321, 'scheme': 'https'}}}}

    def test_verified_settings_are_accepted(self):
        self.check_state(self.valid_state())

    def test_direct_mode_or_missing_auth_is_rejected(self):
        state = self.valid_state()
        state['value']['mode'] = 'direct'
        with self.assertRaisesRegex(RuntimeError, 'refusing to navigate'):
            self.check_state(state)
        state = self.valid_state()
        state['authListener'] = False
        with self.assertRaisesRegex(RuntimeError, 'refusing to navigate'):
            self.check_state(state)

    def test_wrong_proxy_endpoint_is_rejected(self):
        state = self.valid_state()
        state['value']['rules']['singleProxy']['host'] = 'other.invalid'
        with self.assertRaisesRegex(RuntimeError, 'refusing to navigate'):
            self.check_state(state)


if __name__ == '__main__':
    unittest.main()
