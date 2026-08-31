export {};

declare global {
  interface KakaoLatLng {
    readonly __kakaoLatLngBrand: unique symbol;
  }

  interface KakaoMap {
    getLevel(): number;
    panTo(position: KakaoLatLng): void;
    relayout(): void;
    setCenter(position: KakaoLatLng): void;
    setLevel(level: number, options?: { animate?: boolean }): void;
  }

  interface KakaoCustomOverlay {
    setMap(map: KakaoMap | null): void;
    setPosition(position: KakaoLatLng): void;
  }

  interface KakaoMapsNamespace {
    load(callback: () => void): void;
    LatLng: new (latitude: number, longitude: number) => KakaoLatLng;
    Map: new (
      container: HTMLElement,
      options: { center: KakaoLatLng; level: number },
    ) => KakaoMap;
    CustomOverlay: new (options: {
      content: HTMLElement;
      map: KakaoMap;
      position: KakaoLatLng;
      yAnchor?: number;
      zIndex?: number;
    }) => KakaoCustomOverlay;
  }

  interface Window {
    kakao?: { maps: KakaoMapsNamespace };
  }
}
