// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { BaseImageFilter } from './image-processing';

export interface GaussianBlurOptions {
    kernelSize: number;
    sigma: number;
}

export default class GaussianBlurImplementation extends BaseImageFilter {
    private cv: any;
    private kernelSize = 5;
    private sigma = 0;

    constructor(cv: any) {
        super();
        this.cv = cv;
    }

    public configure(options: Partial<GaussianBlurOptions>): void {
        if (typeof options.kernelSize === 'number') {
            // the kernel size must be a positive odd integer
            const size = Math.max(1, Math.round(options.kernelSize));
            this.kernelSize = size % 2 === 0 ? size + 1 : size;
        }

        if (typeof options.sigma === 'number') {
            this.sigma = options.sigma;
        }
    }

    public processImage(src: ImageData, frameNumber: number): ImageData {
        const { cv } = this;
        let matImage = null;
        const RGBADist = new cv.Mat();
        try {
            this.currentProcessedImage = frameNumber;
            matImage = cv.matFromImageData(src);
            const ksize = new cv.Size(this.kernelSize, this.kernelSize);
            // blurring is applied to the RGBA image directly; the constant alpha channel is unaffected
            cv.GaussianBlur(matImage, RGBADist, ksize, this.sigma, this.sigma, cv.BORDER_DEFAULT);
            const arr = new Uint8ClampedArray(RGBADist.data, RGBADist.cols, RGBADist.rows);
            return new ImageData(arr, src.width, src.height);
        } catch (e: unknown) {
            throw e instanceof Error ? e : new Error('Unknown error');
        } finally {
            if (matImage) {
                matImage.delete();
            }

            RGBADist.delete();
        }
    }
}
