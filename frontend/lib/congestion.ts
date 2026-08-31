import type { CongestionLevel } from '@/types/api';

export interface CongestionPresentation {
  label: string;
  className: string;
}

const PRESENTATIONS: Record<CongestionLevel, CongestionPresentation> = {
  LOW: { label: '여유', className: 'low' },
  MEDIUM: { label: '보통', className: 'medium' },
  HIGH: { label: '혼잡', className: 'high' },
  UNKNOWN: { label: '수집 대기', className: 'unknown' },
};

export const getCongestionPresentation = (
  level: CongestionLevel,
): CongestionPresentation => PRESENTATIONS[level] || PRESENTATIONS.UNKNOWN;
