// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { BaseImageFilter } from './image-processing';

export interface UnsharpMaskOptions {
    amount: number;
    kernelSize: number;
    sigma: number;
}

// Unsharp masking: sharpened = (1 + amount)*src - amount*GaussianBlur(src).
// Used together with CLAHE as the guideline's "CLAHE -> Unsharp" reference view.
export default class UnsharpMaskImplementation extends BaseImageFilter {
    private cv: any;
    private amount = 1.0;
    private kernelSize = 5;
    private sigma = 1.0;

    constructor(cv: any) {
        super();
        this.cv = cv;
    }

    public configure(options: Partial<UnsharpMaskOptions>): void {
        if (typeof options.amount === 'number') {
            this.amount = options.amount;
        }

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
        const blurred = new cv.Mat();
        const RGBADist = new cv.Mat();
        try {
            this.currentProcessedImage = frameNumber;
            matImage = cv.matFromImageData(src);
            const ksize = new cv.Size(this.kernelSize, this.kernelSize);
            cv.GaussianBlur(matImage, blurred, ksize, this.sigma, this.sigma, cv.BORDER_DEFAULT);
            // addWeighted saturates per channel; the constant alpha is preserved: (1+a)*255 - a*255 = 255
            cv.addWeighted(matImage, 1 + this.amount, blurred, -this.amount, 0, RGBADist);
            const arr = new Uint8ClampedArray(RGBADist.data, RGBADist.cols, RGBADist.rows);
            return new ImageData(arr, src.width, src.height);
        } catch (e: unknown) {
            throw e instanceof Error ? e : new Error('Unknown error');
        } finally {
            if (matImage) {
                matImage.delete();
            }

            blurred.delete();
            RGBADist.delete();
        }
    }
}
