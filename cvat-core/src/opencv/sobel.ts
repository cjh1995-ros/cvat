// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { BaseImageFilter } from './image-processing';

export interface SobelOptions {
    kernelSize: number;
}

// Sobel gradient magnitude (|Gx| + |Gy|) rendered as a grayscale image.
// This is the same gradient family the planned edge-correction step relies on,
// so the view doubles as a preview of where snapping has signal.
export default class SobelImplementation extends BaseImageFilter {
    private cv: any;
    private kernelSize = 3;

    constructor(cv: any, kernelSize = 3) {
        super();
        this.cv = cv;
        this.configure({ kernelSize });
    }

    public configure(options: Partial<SobelOptions>): void {
        if (typeof options.kernelSize === 'number') {
            // the kernel size must be a positive odd integer (1, 3, 5, 7)
            const size = Math.max(1, Math.round(options.kernelSize));
            this.kernelSize = size % 2 === 0 ? size + 1 : size;
        }
    }

    public processImage(src: ImageData, frameNumber: number): ImageData {
        const { cv } = this;
        let matImage = null;
        const gray = new cv.Mat();
        const gradX = new cv.Mat();
        const gradY = new cv.Mat();
        const absX = new cv.Mat();
        const absY = new cv.Mat();
        const grad = new cv.Mat();
        const RGBADist = new cv.Mat();
        try {
            this.currentProcessedImage = frameNumber;
            matImage = cv.matFromImageData(src);
            cv.cvtColor(matImage, gray, cv.COLOR_RGBA2GRAY, 0);
            // compute on CV_16S to avoid clipping negative gradients, then take absolute value
            cv.Sobel(gray, gradX, cv.CV_16S, 1, 0, this.kernelSize, 1, 0, cv.BORDER_DEFAULT);
            cv.Sobel(gray, gradY, cv.CV_16S, 0, 1, this.kernelSize, 1, 0, cv.BORDER_DEFAULT);
            cv.convertScaleAbs(gradX, absX, 1, 0);
            cv.convertScaleAbs(gradY, absY, 1, 0);
            cv.addWeighted(absX, 0.5, absY, 0.5, 0, grad);
            cv.cvtColor(grad, RGBADist, cv.COLOR_GRAY2RGBA, 0);
            const arr = new Uint8ClampedArray(RGBADist.data, RGBADist.cols, RGBADist.rows);
            return new ImageData(arr, src.width, src.height);
        } catch (e: unknown) {
            throw e instanceof Error ? e : new Error('Unknown error');
        } finally {
            if (matImage) {
                matImage.delete();
            }

            gray.delete();
            gradX.delete();
            gradY.delete();
            absX.delete();
            absY.delete();
            grad.delete();
            RGBADist.delete();
        }
    }
}
