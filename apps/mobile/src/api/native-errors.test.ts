import { errorMessage, friendlyNativeError } from './client';

describe('friendlyNativeError', () => {
  it('replaces the Huawei health service failure with something a patient can act on', () => {
    const message = friendlyNativeError('Binding to service failed');

    expect(message).toContain('连不上健康服务');
    expect(message).toContain('华为运动健康');
  });

  it('recognises the missing InstanceId error the same way', () => {
    const raw =
      'Fetching the token failed: java.util.concurrent.ExecutionException: java.io.IOException: MISSING_INSTANCEID_SERVICE';

    expect(friendlyNativeError(raw)).toContain('连不上健康服务');
  });

  it('keeps one wording for any raw Java exception', () => {
    expect(
      friendlyNativeError('java.lang.IllegalStateException: boom'),
    ).toBe('手机的系统服务返回了错误，稍后再试一次。');
  });

  it('leaves messages it does not understand alone', () => {
    expect(friendlyNativeError('Health Connect 读取失败')).toBeNull();
    expect(friendlyNativeError('游标已失效')).toBeNull();
  });
});

describe('errorMessage', () => {
  it('translates the native failures that reach the screen', () => {
    expect(errorMessage(new Error('Binding to service failed'))).toContain(
      '连不上健康服务',
    );
  });

  it('keeps a message the app already wrote in Chinese', () => {
    expect(errorMessage(new Error('Health Connect 读取失败'))).toBe(
      'Health Connect 读取失败',
    );
  });

  it('falls back when it gets something that is not an Error', () => {
    expect(errorMessage('boom')).toBe('发生了未知错误');
  });
});
