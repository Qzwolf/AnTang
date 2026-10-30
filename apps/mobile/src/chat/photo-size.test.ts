import { manipulateAsync } from 'expo-image-manipulator';
import { Image } from 'react-native';

import { MAX_PHOTO_EDGE, shrinkPhoto } from './photo-size';
import type { DraftAttachment } from './attachment-flows';

jest.mock('expo-image-manipulator', () => ({
  SaveFormat: { JPEG: 'jpeg' },
  manipulateAsync: jest.fn(),
}));

const manipulate = manipulateAsync as jest.Mock;

function photo(overrides: Partial<DraftAttachment> = {}): DraftAttachment {
  return {
    kind: 'photo',
    mimeType: 'image/jpeg',
    name: 'IMG_0001.jpg',
    uri: 'file:///photos/original.jpg',
    ...overrides,
  };
}

function size(width: number, height: number) {
  jest
    .spyOn(Image, 'getSize')
    .mockImplementation((_uri, onLoad) => onLoad(width, height));
}

beforeEach(() => {
  jest.restoreAllMocks();
  manipulate.mockReset();
  manipulate.mockResolvedValue({ uri: 'file:///cache/small.jpg' });
});

test('a big landscape photo is scaled down to the long edge', async () => {
  size(4032, 3024);

  const prepared = await shrinkPhoto(photo());

  expect(manipulate).toHaveBeenCalledWith(
    'file:///photos/original.jpg',
    [{ resize: { width: MAX_PHOTO_EDGE } }],
    { compress: 0.82, format: 'jpeg' },
  );
  expect(prepared.uri).toBe('file:///cache/small.jpg');
  expect(prepared.mimeType).toBe('image/jpeg');
  expect(prepared.name).toBe('IMG_0001.jpg');
});

test('a tall photo is scaled by its height, not its width', async () => {
  size(1080, 1920);

  await shrinkPhoto(photo());

  expect(manipulate).toHaveBeenCalledWith(
    'file:///photos/original.jpg',
    [{ resize: { height: MAX_PHOTO_EDGE } }],
    expect.anything(),
  );
});

test('a photo that is already small is sent as it is', async () => {
  size(1024, 768);

  const prepared = await shrinkPhoto(photo());

  expect(manipulate).not.toHaveBeenCalled();
  expect(prepared.uri).toBe('file:///photos/original.jpg');
});

test('a report is never resized', async () => {
  size(4032, 3024);

  const prepared = await shrinkPhoto(
    photo({ kind: 'report', mimeType: 'application/pdf', name: '报告.pdf' }),
  );

  expect(manipulate).not.toHaveBeenCalled();
  expect(prepared.name).toBe('报告.pdf');
});

test('a photo whose size cannot be read still goes out', async () => {
  jest
    .spyOn(Image, 'getSize')
    .mockImplementation((_uri, _onLoad, onError) => onError?.(new Error('gone')));

  const prepared = await shrinkPhoto(photo());

  expect(prepared.uri).toBe('file:///photos/original.jpg');
  expect(manipulate).not.toHaveBeenCalled();
});

test('a failed resize falls back to the original instead of blocking the send', async () => {
  size(4032, 3024);
  manipulate.mockRejectedValue(new Error('no memory'));

  const prepared = await shrinkPhoto(photo());

  expect(prepared.uri).toBe('file:///photos/original.jpg');
  expect(prepared.mimeType).toBe('image/jpeg');
});
