const KAKAO_SCRIPT_ID = 'kakao-maps-sdk';
let sdkPromise: Promise<KakaoMapsNamespace> | null = null;
let loadedAppKey: string | null = null;

export class KakaoMapsError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'KakaoMapsError';
  }
}

const finishKakaoLoad = (
  resolve: (maps: KakaoMapsNamespace) => void,
  reject: (error: Error) => void,
) => {
  if (!window.kakao?.maps) {
    reject(new KakaoMapsError('카카오맵 SDK를 초기화하지 못했습니다.'));
    return;
  }
  window.kakao.maps.load(() => resolve(window.kakao!.maps));
};

export const loadKakaoMapsSdk = (
  appKey: string,
): Promise<KakaoMapsNamespace> => {
  if (typeof window === 'undefined') {
    return Promise.reject(
      new KakaoMapsError('카카오맵은 브라우저에서만 사용할 수 있습니다.'),
    );
  }
  if (!appKey) {
    return Promise.reject(
      new KakaoMapsError('카카오맵 JavaScript 키가 설정되지 않았습니다.'),
    );
  }
  if (sdkPromise && loadedAppKey === appKey) return sdkPromise;
  if (loadedAppKey && loadedAppKey !== appKey) {
    return Promise.reject(
      new KakaoMapsError('카카오맵 키가 이미 다른 값으로 초기화되었습니다.'),
    );
  }

  loadedAppKey = appKey;
  sdkPromise = new Promise<KakaoMapsNamespace>((resolve, reject) => {
    if (window.kakao?.maps) {
      finishKakaoLoad(resolve, reject);
      return;
    }

    const existing = document.getElementById(
      KAKAO_SCRIPT_ID,
    ) as HTMLScriptElement | null;
    const script = existing || document.createElement('script');

    const handleLoad = () => finishKakaoLoad(resolve, reject);
    const handleError = () =>
      reject(new KakaoMapsError('카카오맵 SDK를 불러오지 못했습니다.'));

    script.addEventListener('load', handleLoad, { once: true });
    script.addEventListener('error', handleError, { once: true });

    if (!existing) {
      script.id = KAKAO_SCRIPT_ID;
      script.async = true;
      script.src =
        'https://dapi.kakao.com/v2/maps/sdk.js?autoload=false&appkey=' +
        encodeURIComponent(appKey);
      document.head.appendChild(script);
    }
  }).catch((error) => {
    sdkPromise = null;
    loadedAppKey = null;
    throw error;
  });

  return sdkPromise;
};
