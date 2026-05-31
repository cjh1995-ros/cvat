// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { BaseImageFilter } from './image-processing';

export default class GrayscaleImplementation extends BaseImageFilter {
    private cv: any;

    constructor(cv: any) {
        super();
        this.cv = cv;
    }

    public processImage(src: ImageData, frameNumber: number): ImageData {
        const { cv } = this;
        let matImage = null;
        const gray = new cv.Mat();
        const RGBADist = new cv.Mat();
        try {
            this.currentProcessedImage = frameNumber;
            matImage = cv.matFromImageData(src);
            cv.cvtColor(matImage, gray, cv.COLOR_RGBA2GRAY, 0);
            cv.cvtColor(gray, RGBADist, cv.COLOR_GRAY2RGBA, 0);
            const arr = new Uint8ClampedArray(RGBADist.data, RGBADist.cols, RGBADist.rows);
            return new ImageData(arr, src.width, src.height);
        } catch (e: unknown) {
            throw e instanceof Error ? e : new Error('Unknown error');
        } finally {
            if (matImage) {
                matImage.delete();
            }

            gray.delete();
            RGBADist.delete();
        }
    }
}
