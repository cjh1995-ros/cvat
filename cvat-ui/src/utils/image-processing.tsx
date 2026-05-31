// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { ImageProcessing } from 'cvat-core-wrapper';

export enum ImageFilterAlias {
    HISTOGRAM_EQUALIZATION = 'opencv.histogramEqualization',
    GAMMA_CORRECTION = 'fabric.gammaCorrection',
    GRAYSCALE = 'opencv.grayscale',
    GAUSSIAN_BLUR = 'opencv.gaussianBlur',
    CLAHE = 'opencv.clahe',
    CANNY_EDGE = 'opencv.cannyEdge',
    SOBEL_3 = 'opencv.sobel3',
    SOBEL_5 = 'opencv.sobel5',
    UNSHARP_MASK = 'opencv.unsharpMask',
}

export interface ImageFilter {
    modifier: ImageProcessing,
    alias: ImageFilterAlias
}

export function hasFilter(filters: ImageFilter[], alias: ImageFilterAlias): ImageFilter | null {
    const index = filters.findIndex((imageFilter) => imageFilter.alias === alias);
    if (index !== -1) {
        return filters[index];
    }
    return null;
}
